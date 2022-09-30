# coding=utf-8
# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np
import torch

from megatron import get_tokenizer
from megatron.data.bert_dataset import build_training_sample
# from megatron.tokenizer.gpt2_tokenization import GPT2Tokenizer
from megatron.tokenizer.tokenizer import (
    _BertWordPieceTokenizer,
    _GPT2BPETokenizer,
)

# >>>
from lutil import pax
# <<<


class GPTChunkDataset(torch.utils.data.Dataset):

    # def __init__(self, indexed_dataset, chunk_index, eods):
    #     self.indexed_dataset = indexed_dataset
    #     self.chunk_index = chunk_index
    #     self.eods = eods
    def __init__(self, indexed_datasets, dataset_offsets, chunk_index,
                 max_chunk_len):
        self.indexed_datasets = indexed_datasets
        self.dataset_offsets = dataset_offsets
        self.chunk_index = chunk_index
        self.max_gpt_chunk_len = max_chunk_len

        dataset_ids = []
        for i in range(len(dataset_offsets) - 1):
            dataset_ids.append([i] * (dataset_offsets[i+1] - dataset_offsets[i]))
        self.dataset_ids = [ i for ii in dataset_ids for i in ii ]

        # >>>
        self.gpt_tokenizer = _GPT2BPETokenizer(
            vocab_file = "/gpfs/fs1/projects/gpu_adlr/datasets/nlp/gpt3/bpe/gpt2-vocab.json",
            merge_file = "/gpfs/fs1/projects/gpu_adlr/datasets/nlp/gpt3/bpe/gpt2-merges.txt",
        )
        # <<<

        # pax({
        #     "dataset_offsets" : self.dataset_offsets,
        #     "dataset_ids" :
        #     [ "%d / %s ..." % (len(ii), str(ii[:10])) for ii in dataset_ids ],
        #     "*dataset_ids / len" : len(self.dataset_ids),
        # })

    def __len__(self):
        raise Exception("length?")
        # -1 is due to data structure used to retieve the index:
        #    sample i --> [sample_idx[i], sample_idx[i+1])
        return self.sample_idx.shape[0] - 1

    def __getitem__(self, chunk_id):

        # dataset_idx = self.chunk_index_to_dataset_index(chunk_idx)

        dataset_id = self.dataset_ids[chunk_id]
        doc_id, chunk_start_idx, chunk_end_idx, _ = self.chunk_index[chunk_id]
        chunk_len = chunk_end_idx - chunk_start_idx
        indexed_dataset = self.indexed_datasets[dataset_id]

        token_ids = indexed_dataset.get(doc_id,
                                        offset = chunk_start_idx,
                                        length = chunk_len)

        if chunk_len != self.max_gpt_chunk_len:
            assert chunk_len < self.max_gpt_chunk_len, "invalid chunk len."
            token_ids = token_ids.tolist()
            token_ids += [self.gpt_tokenizer.eod_id] * \
                (self.max_gpt_chunk_len - chunk_len)
            # pax({
            #     "tokenizer" : self.gpt_tokenizer,
            #     "token_ids" : "%d ... %s" % (
            #         len(token_ids),
            #         str(token_ids),
            #     ),
            # })

        # pax({
        #     # "indexed_dataset" : indexed_dataset,
        #     "chunk_id" : chunk_id,
        #     "dataset_id" : dataset_id,
        #     "doc_id" : doc_id,
        #     "chunk_start_idx" : chunk_start_idx,
        #     "chunk_end_idx" : chunk_end_idx,
        #     "chunk" : chunk,
        # })

        return {'text': np.array(token_ids, dtype=np.int64)}

class BertChunkDataset(GPTChunkDataset):

    def __init__(self, indexed_datasets, dataset_offsets,
                 chunk_index, max_chunk_len,
                 # max_embed_chunk_len, # ... removed
                 max_seq_len,
                 micro_batch_size,

                 # num_epochs,
                 # max_num_samples,
                 masked_lm_prob,
                 # max_seq_length,
                 # short_seq_prob,
                 seed,
                 binary_head,
    ):

        super().__init__(indexed_datasets, dataset_offsets,
                         chunk_index, max_chunk_len)

        # self.max_bert_chunk_len = max_embed_chunk_len

        # >>>
        # gpt_tokenizer = GPT2Tokenizer(
        # self.gpt_tokenizer = _GPT2BPETokenizer(
        #     vocab_file = "/gpfs/fs1/projects/gpu_adlr/datasets/nlp/gpt3/bpe/gpt2-vocab.json",
        #     merge_file = "/gpfs/fs1/projects/gpu_adlr/datasets/nlp/gpt3/bpe/gpt2-merges.txt",
        # )
        # self.bert_tokenizer = get_tokenizer()
        self.bert_tokenizer = _BertWordPieceTokenizer(
            vocab_file = "/gpfs/fs1/projects/gpu_adlr/datasets/nlp/roberta_mmap/vocab.txt",
            lower_case = True,
        )
        # <<<

        self.max_seq_len = max_seq_len
        self.micro_batch_size = micro_batch_size

        # Params to store.
        self.seed = seed
        self.masked_lm_prob = masked_lm_prob
        # self.max_seq_length = max_seq_length
        self.binary_head = binary_head

        # Vocab stuff.
        # tokenizer = get_tokenizer()
        self.vocab_id_list = list(self.bert_tokenizer.inv_vocab.keys())
        self.vocab_id_to_token_dict = self.bert_tokenizer.inv_vocab
        self.cls_id = self.bert_tokenizer.cls
        self.sep_id = self.bert_tokenizer.sep
        self.mask_id = self.bert_tokenizer.mask
        self.pad_id = self.bert_tokenizer.pad

        # Sort samples by bert chunk length.
        bert_chunk_lens = list(enumerate(self.chunk_index[:, 3]))
        print(" > sort / start.")
        import time
        t = time.time()
        bert_chunk_lens.sort(key = lambda item : item[1])
        # >>>
        bert_chunk_lens.reverse() # for debugging.
        # <<<
        self.sample_idxs = [ item[0] for item in bert_chunk_lens ]
        print(" > sort / end. [ %.2f sec ]" % (time.time() - t))

        # Group samples idxs into microbatches.
        n_chunks = len(self.sample_idxs)
        # batch_start_idxs = list(range(0, n_chunks, micro_batch_size))
        # batch_end_idxs = [min(n_chunks, i + micro_batch_size)
        #                   for i in batch_start_idxs]
        # self.batch_idxs = [ i // micro_batch_size for i in range(n_chunks) ]
        self.batch_chunk_lens = []
        for batch_start_idx in range(0, n_chunks, micro_batch_size):
            batch_end_idx = min(n_chunks, batch_start_idx + micro_batch_size)
            batch_chunk_lens = [item[1].item() for item in
                                bert_chunk_lens[batch_start_idx:batch_end_idx]]
            max_chunk_len = max(batch_chunk_lens)
            self.batch_chunk_lens.append(max_chunk_len)

            # >>>
            # if min(batch_chunk_lens) != max_chunk_len:
            #     pax({
            #         "min chunk len" : min(batch_chunk_lens),
            #         "max chunk len" : max_chunk_len,
            #     })
            # <<<

        # pax({
        #     "n_chunks" : n_chunks,
        #     "n_batches" : int(np.ceil(n_chunks / micro_batch_size)),
        #     # "batch_start_idxs" : str(batch_start_idxs),
        #     # "batch_end_idxs" : str(batch_end_idxs),
        #     # "batch_idxs" : str(self.batch_idxs),
        #     "batch_chunk_lens" : str(self.batch_chunk_lens),
        # })

        # print(np.array(bert_chunk_lens))
        # pax({
        #     "chunk_index" : chunk_index,
        #     # "bert_chunk_lens" : bert_chunk_lens,
        # })
        # raise Exception("sort by bert length.")


    # def __getitem__(self, chunk_id):

    #     gpt_token_ids = super().__getitem__(chunk_id)["text"]
    #     gpt_token_ids = [t for t in gpt_token_ids.tolist()
    #                      if t != self.gpt_tokenizer.eod]

    #     text = self.gpt_tokenizer.detokenize(gpt_token_ids)

    #     bert_token_ids = self.bert_tokenizer.tokenize(text)
    #     # >>>
    #     # bert_chunk_len = len(bert_token_ids)
    #     # if bert_chunk_len != self.max_chunk_len:
    #     #     assert bert_chunk_len < self.max_chunk_len, "invalid chunk len."
    #     #     bert_token_ids += [self.bert_tokenizer.eos_token_id] * \
    #     #         (self.max_chunk_len - bert_chunk_len)
    #     #     # pax({
    #     #     #     "bert_tokenizer" : self.bert_tokenizer,
    #     #     #     "bert_token_ids" : "%d ... %s" % (
    #     #     #         len(bert_token_ids),
    #     #     #         str(bert_token_ids),
    #     #     #     ),
    #     #     # })
    #     # +++
    #     # pax({
    #     #     "max_chunk_len" : self.max_chunk_len,
    #     #     "max_embed_chunk_len" : self.max_embed_chunk_len,
    #     # })

    #     # Final token will be padded in 'build_sample'.
    #     # assert len(bert_token_ids) <= self.max_chunk_len - 2 # cls, sep[, eos]

    #     # >>>
    #     # Cls, Sep [+pad_id] need to be added.
    #     if 0: # original code.
    #         assert len(bert_token_ids) <= self.max_bert_chunk_len - 2, \
    #             "tokens %d, max tokens %d." % (len(bert_token_ids),
    #                                            self.max_bert_chunk_len)
    #     else:
    #         if len(bert_token_ids) > self.max_bert_chunk_len - 2:
    #             print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~")
    #             print("~~~~ text ~~~~")
    #             print(text)
    #             print("~~~~ gpt token ids [ %d ] ~~~~" % len(gpt_token_ids))
    #             [print("%d ... '%s'" % (t, self.gpt_tokenizer.detokenize([t])))
    #              for t in gpt_token_ids]
    #             print("~~~~ bert token ids [ %d ] ~~~~" % len(bert_token_ids))
    #             [print("%d ... '%s'" % (t, self.bert_tokenizer.detokenize([t])))
    #              for t in bert_token_ids]
    #             raise Exception("bert overflow.")
    #     # <<<

    #     # >>>>>>>> haaaaaaaaaack. >>>>>>>>
    #     if len(bert_token_ids) == 0:
    #         # raise Exception("hi, empty bert.")
    #         bert_token_ids = [ self.pad_id ]
    #     # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

    #     # pax({
    #     #     "gpt_token_ids" : gpt_token_ids,
    #     #     "bert_token_ids" : bert_token_ids,
    #     #     "gpt_token_ids / str" : str(gpt_token_ids.tolist()),
    #     #     "bert_token_ids / str" : str(bert_token_ids.tolist()),
    #     #     "text" : text,
    #     # })

    #     # >>>
    #     # return {'text': np.array(bert_token_ids, dtype=np.int64)}
    #     # <<<

    #     # Note that this rng state should be numpy and not python since
    #     # python randint is inclusive whereas the numpy one is exclusive.
    #     # We % 2**32 since numpy requres the seed to be between 0 and 2**32 - 1
    #     np_rng = np.random.RandomState(seed=((self.seed + chunk_id) % 2**32))

    #     # >>>
    #     # pax(0, {
    #     #     "start_idx" : int(start_idx),
    #     #     "end_idx" : int(end_idx),
    #     #     "seq_length" : int(seq_length),
    #     #     "indexed_dataset / %d" % start_idx : self.indexed_dataset[start_idx],
    #     #     "sample" : sample,
    #     #     "seed" : self.seed,
    #     #     # "seq_length" : seq_length,
    #     #     # "max_seq_length" : self.max_seq_length,
    #     #     # "vocab_id_list" : self.vocab_id_list,
    #     #     # "vocab_id_to_token_dict" : self.vocab_id_to_token_dict,
    #     #     # "cls_id" : self.cls_id,
    #     #     # "sep_id" : self.sep_id,
    #     #     # "mask_id" : self.mask_id,
    #     #     # "pad_id" : self.pad_id,
    #     #     # "masked_lm_prob" : self.masked_lm_prob,
    #     #     # "np_rng" : np_rng,
    #     #     # "binary_head" : self.binary_head,
    #     # })
    #     # <<<

    #     try:
    #         sample = build_training_sample([bert_token_ids],
    #                                        len(bert_token_ids), # self.max_chunk_len,
    #                                        self.max_bert_chunk_len, # for padding
    #                                        self.vocab_id_list,
    #                                        self.vocab_id_to_token_dict,
    #                                        self.cls_id, self.sep_id,
    #                                        self.mask_id, self.pad_id,
    #                                        self.masked_lm_prob, np_rng,
    #                                        self.binary_head)
    #     except Exception as e:
    #         print("~~~ text = '%s'. ~~~" % text)
    #         pax({
    #             "text" : text,
    #             "gpt_token_ids" : gpt_token_ids,
    #             "bert_token_ids" : bert_token_ids,
    #         })

    #     # pax({"sample": sample})

    #     return sample
    def __getitem__(self, sample_id):

        chunk_id = self.sample_idxs[sample_id]

        gpt_token_ids = super().__getitem__(chunk_id)["text"]
        gpt_token_ids = [t for t in gpt_token_ids.tolist()
                         if t != self.gpt_tokenizer.eod]

        text = self.gpt_tokenizer.detokenize(gpt_token_ids)

        bert_token_ids = self.bert_tokenizer.tokenize(text)

        # Note that this rng state should be numpy and not python since
        # python randint is inclusive whereas the numpy one is exclusive.
        # We % 2**32 since numpy requres the seed to be between 0 and 2**32 - 1
        np_rng = np.random.RandomState(seed=((self.seed + chunk_id) % 2**32))

        batch_id = sample_id // self.micro_batch_size
        batch_chunk_len = min(self.max_seq_len - 2, self.batch_chunk_lens[batch_id])

        if len(bert_token_ids) > batch_chunk_len:
            # raise Exception("long, %d -> %d." % (len(bert_token_ids), batch_chunk_len))
            bert_token_ids = bert_token_ids[:batch_chunk_len]

        # pax({
        #     "batch_id" : batch_id,
        #     "batch_chunk_len" : batch_chunk_len,
        #     "id / 0" : chunk_id,
        #     "id / 1" : self.sample_idxs[chunk_id],
        #     "gpt_token_ids":"%d / %s"%(len(gpt_token_ids),str(gpt_token_ids)),
        #     "bert_token_ids":"%d / %s"%(len(bert_token_ids),str(bert_token_ids)),
        # })

        sample = build_training_sample([bert_token_ids],
                                       len(bert_token_ids), # self.max_chunk_len,
                                       # self.max_bert_chunk_len, # for padding
                                       # len(bert_token_ids) + 2, # +2=cls,sep
                                       batch_chunk_len + 2, # +2=cls,sep
                                       self.vocab_id_list,
                                       self.vocab_id_to_token_dict,
                                       self.cls_id, self.sep_id,
                                       self.mask_id, self.pad_id,
                                       self.masked_lm_prob, np_rng,
                                       self.binary_head)

        # pax({"sample": sample})

        return sample
