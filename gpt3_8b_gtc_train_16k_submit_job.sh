#!/bin/bash

export NCCL_IB_SL=1
export CUDA_DEVICE_MAX_CONNECTIONS=1

CKPT="gpt3-8b-multi-1.1t-gtc"
NAME="$CKPT-itp-16k-lr1e-5"

DIR=`pwd`
export SUBMIT_LOGS="$DIR/logs"
DATETIME=`date +'date_%y-%m-%d_time_%H-%M-%S'`
mkdir -p $DIR/logs

CHECKPOINT_DIR="/lustre/fsw/adlr/adlr-nlp/adlr-nlp-sharing/nvllm-1.1t/checkpoints/${CKPT}"
SAVE_DIR="$DIR/checkpoints/${NAME}"

if [[ -f "$SAVE_DIR/latest_checkpointed_iteration.txt" ]]; then
    CHECKPOINT_DIR=${SAVE_DIR}
    opt=""
else
    opt="--no-load-rng \
    --no-load-optim \
    --finetune"
fi

mkdir -p $SAVE_DIR

TENSORBOARD_DIR="$DIR/tensorboard/${NAME}"
mkdir -p ${TENSORBOARD_DIR}

# Get the data blend
. /lustre/fsw/adlr/adlr-nlp/adlr-nlp-sharing/nvllm-1.1t/data/tokens/multi-1.1t-gtc-blend-v0.1.sh

options="$opt \
    --sequence-parallel \
    --use-flash-attn \
    --recompute-activations \
    --apply-layernorm-1p \
    --untie-embeddings-and-output-weights \
    --disable-bias-linear \
    --no-position-embedding \
    --use-rotary-position-embeddings \
    --rotary-percent 0.5 \
    --swiglu \
    --attention-dropout 0.0 \
    --hidden-dropout 0.0 \
    --exit-duration-in-mins 230 \
    --tensor-model-parallel-size 4 \
    --pipeline-model-parallel-size 1 \
    --num-layers 32 \
    --hidden-size 4096 \
    --num-attention-heads 32 \
    --micro-batch-size 1 \
    --global-batch-size 256 \
    --train-samples 268554688 \
    --lr-decay-samples 255126953 \
    --lr 1.0e-5 \
    --min-lr 1.0e-6 \
    --lr-decay-style cosine \
    --log-interval 2 \
    --eval-interval 10 \
    --tokenizer-type GPTSentencePieceTokenizer \
    --tokenizer-model /lustre/fsw/adlr/adlr-nlp/adlr-nlp-sharing/nvllm-1.1t/utils/mt_nlg_plus_multilingual_ja_zh_the_stack_frac_015_256k.model \
    --data-path ${DATA_BLEND} \
    --save-interval 100 \
    --save ${SAVE_DIR} \
    --load ${CHECKPOINT_DIR} \
    --split 99,1,0 \
    --clip-grad 1.0 \
    --weight-decay 0.1 \
    --adam-beta1 0.9 \
    --adam-beta2 0.95 \
    --init-method-std 0.010 \
    --log-params-norm \
    --log-num-zeros-in-grad \
    --bf16 \
    --DDP-impl local \
    --tensorboard-dir ${TENSORBOARD_DIR}"

options=" \
    --seq-length 16384 \
    --max-position-embeddings 16384 \
    --rotary-seq-len-interpolation-factor 4 \
    --distributed-timeout-minutes 30 \
    --use-distributed-optimizer \
    --eval-iters 10 \
    $options"

run_cmd="python -u ${DIR}/pretrain_gpt.py ${options}"
# run_cmd="python -m torch.distributed.launch --nproc_per_node 8 ${DIR}/pretrain_gpt.py ${options}"
# ${run_cmd}
LAUNCH="$ADLR_UTILS/mp_launch"
submit_job --gpu 8 --nodes 32 --email_mode never  --mounts "/lustre/fsw/adlr" --partition luna --image "gitlab-master.nvidia.com/adlr/megatron-lm/pytorch:22.04-py3-eval" -c "$LAUNCH ${run_cmd}" -n "${NAME}" --duration 4 --exclude luna-0253  #  --dependent_clones 3
# srun -l \
#      --container-image "gitlab-master.nvidia.com/adlr/megatron-lm/pytorch:23.04-py3-jbarker-revilm" \
#      --container-mounts "/lustre/fsw/adlr:/lustre/fsw/adlr" \
#      --output=$DIR/logs/%x_%j_$DATETIME.log sh -c "${run_cmd}"
# 
# set +x
