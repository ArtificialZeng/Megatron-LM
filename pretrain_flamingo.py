# Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.

"""Pretrain Flamingo"""

import torch
from functools import partial
from megatron import get_args
from megatron import print_rank_0
from megatron import get_timers
from megatron import get_tokenizer
from megatron.core import tensor_parallel, mpu
from megatron.data.dataset_utils import build_train_valid_test_datasets, build_multiple_valid_datasets
from megatron.model import FlamingoModel, ModelType
from megatron.training import pretrain
from megatron.utils import get_ltor_masks_and_position_ids
from megatron.utils import average_losses_across_data_parallel_group
from megatron.model.vision.vit_backbone import CLIPVitBackbone, SAMViTBackbone
from megatron.data.blendable_dataset import BlendableDataset

def model_provider(pre_process=True, post_process=True):
    """Build the model."""

    print_rank_0('building Flamingo model ...')
    model = FlamingoModel(
        num_tokentypes=0,
        parallel_output=True,
        pre_process=pre_process,
        post_process=post_process
    )
    return model

def visual_model_provider(visual_arch, pre_process=True, post_process=False):
    """Build the visual model."""
    
    if visual_arch.startswith("SAM"):
        visual_model = SAMViTBackbone(pre_process=pre_process, 
                                   post_process=post_process)
    else:
        visual_model = CLIPViTBackbone(pre_process=pre_process, 
                                   post_process=post_process)

    print_rank_0('building visual model....')
    return visual_model

def get_batch(data_iterator, visual_model, model_has_data_iter=True):
    """Generate a batch"""

    args = get_args()
    
    tokens = None
    labels = None
    loss_mask = None
    attention_mask = None
    position_ids = None

    if model_has_data_iter:
            
        # Broadcast data.
        if data_iterator is not None:
            data = next(data_iterator)
        else:
            data = None

        data_text = tensor_parallel.broadcast_data(["text"], data, torch.int64)["text"]
        data_img = tensor_parallel.broadcast_data(["img"], data, torch.float32)
        #weights = tensor_parallel.broadcast_data(["weights"], data, torch.float32)["weights"]
        prompt_len = tensor_parallel.broadcast_data(["prompt_len"], data, torch.int64)["prompt_len"]

        # Unpack.
        tokens_ = data_text.long()
        img_raw = data_img['img'].reshape(-1, 3, args.img_h, args.img_w)

        if img_raw is None:
            img_tokens = None
        else:
            img_tokens = visual_model(img_raw).transpose(0, 1).contiguous()

        tokenizer = get_tokenizer()
        tokens = tokens_[:, :args.seq_length].contiguous()
        labels = tokens_[:, 1:args.seq_length+1].contiguous()
    
        attention_mask, loss_mask, position_ids = \
            get_ltor_masks_and_position_ids(tokens, tokenizer.eod,
                                            args.reset_position_ids,
                                            args.reset_attention_mask,
                                            args.eod_mask_loss,
                                            question_length=prompt_len)

    return tokens, labels, img_tokens, loss_mask, attention_mask, position_ids

def loss_func(loss_mask, output_tensor):
    losses = output_tensor.float()
    if loss_mask is not None:
        loss_mask = loss_mask.view(-1).float()
        loss = torch.sum(losses.view(-1) * loss_mask) / loss_mask.sum()
    else:
        loss = torch.mean(losses)

    # Reduce loss for logging.
    averaged_loss = average_losses_across_data_parallel_group([loss])

    return loss, {'lm loss': averaged_loss[0]}


def forward_step(data_iterator, model, visual_model):
    """Forward step."""
    args = get_args()
    timers = get_timers()

    # Get the batch.
    timers('batch-generator', log_level=2).start()
    tokens, labels, img_tokens, loss_mask, attention_mask, position_ids = get_batch(
        data_iterator, model_has_data_iter=model.has_data_iter, visual_model=visual_model)
    timers('batch-generator').stop()

    output_tensor = model(tokens, img_tokens, position_ids, attention_mask,
                          labels=labels)

    return output_tensor, partial(loss_func, loss_mask)


def train_valid_test_datasets_provider(train_val_test_num_samples):
    """Build train, valid, and test datasets."""
    args = get_args()

    print_rank_0('> building train, validation, and test datasets '
                 'for Flamingo ...')
    train_ds1, valid_ds, test_ds = build_train_valid_test_datasets(
        data_prefix=args.data_path,
        data_impl=args.data_impl,
        splits_string=args.split,
        train_valid_test_num_samples=train_val_test_num_samples,
        max_seq_length=args.ds_seq_length,
        seed=args.seed,
        skip_warmup=(not args.mmap_warmup),
        dataset_type='multimodal')
    print_rank_0("> finished creating Flamingo datasets ...")

    if args.valid_path is not None:
        _, valid_list, _ = build_multiple_valid_datasets(
            data_prefix=args.valid_path,
            data_impl="mmap",
            splits_string="0,100,0",
            train_valid_test_num_samples=train_val_test_num_samples,
            max_seq_length=args.ds_seq_length,
            seed=args.seed,
            skip_warmup=(not args.mmap_warmup),
            dataset_type='multimodal')
        
        valid_ds = []
        for valid_set in valid_list:
            valid_ds.append(BlendableDataset([valid_set], [args.weight]))
        print("Customized Val set.....")
    else:
        valid_ds = [BlendableDataset([valid_ds], [args.weight])]

    train_ds = BlendableDataset([train_ds1], [args.weight])
    return train_ds, valid_ds, test_ds

def add_validation_args(parser):
    """Text generation arguments."""
    group = parser.add_argument_group(title='validation set')
    group.add_argument('--valid-path', nargs='*', default=None,
                       help='Path to the training dataset. Accepted format:'
                       '1) a single data path, 2) multiple datasets in the'
                       'form: dataset1-weight dataset1-path dataset2-weight '
                       'dataset2-path ...')
    group.add_argument('--train-eval-path', nargs='*', default=None)
    group.add_argument('--prompt-path', type=str, default=None)
    group.add_argument('--dset-config', type=str, default=None)
    group.add_argument('--weight', type=float, default=1)
    group.add_argument('--adaptor', action='store_true', default=False)
    group.add_argument('--aug', action='store_true', default=False)
    group.add_argument('--eval-ppl', action='store_true', default=False)
    group.add_argument('--project-size', type=int, default=256)
    group.add_argument('--cyclic-train-iters', type=int, default=None)
    group.add_argument('--stored_params', type=dict, default=dict())
    group.add_argument('--eval_ppl', action='store_true', default=False)
    group.add_argument('--debug', action='store_true', default=False)
    group.add_argument('--add_retriever', action='store_true', default=False)
    group.add_argument('--return_doc_ids', action='store_true', default=False)
    group.add_argument('--return_neighbor_ids', action='store_true', default=False)
    group.add_argument('--add_offset_doc_ids', action='store_true', default=False)
    group.add_argument('--offset_dict_path', type=str, default='')
    group.add_argument('--neighbors_path', type=str, default='')
    group.add_argument('--valid_neighbors_path', type=str, default='')
    group.add_argument('--database_path', type=str, default='')
    group.add_argument('--valid_database_path', type=str, default='')
    group.add_argument('--encoder-layers', type=int, default=12)
    group.add_argument('--encoder-hidden-dropout', type=float, default=0.1)
    group.add_argument('--encoder-attention-dropout', type=float, default=0.1)
    group.add_argument('--perceiver-type', type=str, default='cross-attn')
    group.add_argument('--k', type=int, default=2)
    group.add_argument('--r', type=int, default=128)
    group.add_argument('--m', type=int, default=64)
    group.add_argument('--print-freq', type=int, default=500)

    return parser

if __name__ == "__main__":

    pretrain(train_valid_test_datasets_provider, model_provider,
             ModelType.encoder_or_decoder,
             forward_step,
             args_defaults={'tokenizer_type': 'GPT2BPETokenizer'},
             visual_model_provider=visual_model_provider,
             extra_args_provider=add_validation_args)
