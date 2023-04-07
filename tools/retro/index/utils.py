# Copyright (c) 2023, NVIDIA CORPORATION.  All rights reserved.

import concurrent
import gc
import glob
import numpy as np
import os
import psutil
import time
import torch
from tqdm import tqdm

from megatron import get_retro_args, print_rank_0
from tools.retro.db.utils import get_indexed_dataset_infos
from tools.retro.external_libs import h5py


def get_index_dir():
    """Create sub-directory for this index."""

    args = get_retro_args()

    # Directory path.
    index_dir_path = os.path.join(
        args.retro_workdir,
        "index",
        args.retro_index_type,
        # >>>
        # "%s_t%d" % (args.retro_index_str, get_num_training_vecs()),
        # "%s_t%.3f" % (args.retro_index_str, args.retro_index_train_load_fraction),
        args.retro_index_str,
        # <<<
    )

    # Make directory.
    os.makedirs(index_dir_path, exist_ok=True)

    return index_dir_path


def num_samples_to_block_ranges(num_samples):
    '''Split a range (length num_samples) into sequence of block ranges
    of size block_size.'''
    args = get_retro_args()
    block_size = args.retro_block_size
    start_idxs = list(range(0, num_samples, block_size))
    end_idxs = [min(num_samples, s + block_size) for s in start_idxs]
    ranges = list(zip(start_idxs, end_idxs))
    return ranges


# >>>
# def get_training_data_dir():
def get_training_data_root_dir():
    args = get_retro_args()
    return os.path.join(args.retro_workdir, "index", "train_emb")


def get_training_data_block_dir():
    return os.path.join(get_training_data_root_dir(), "blocks")


# def get_training_data_paths():
def get_training_data_block_paths():
    return sorted(glob.glob(get_training_data_block_dir() + "/*.hdf5"))


# def get_training_data_bin_path():
def get_training_data_merged_path():
    args = get_retro_args()
    return os.path.join(get_training_data_root_dir(),
                        "train_%.3f.bin" % args.retro_index_train_load_fraction)
# <<<


def get_added_codes_dir():
    return os.path.join(get_index_dir(), "add_codes")


def get_added_code_paths():
    return sorted(glob.glob(get_added_codes_dir() + "/*.hdf5"))


# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# def get_training_data_group_infos():

#     args = get_retro_args()

#     block_paths = get_training_data_paths()
#     max_group_size = args.retro_index_train_block_size

#     groups = []
#     group = []
#     group_size = 0
#     for block_path in block_paths:
#         with h5py.File(block_path) as f:
#             block_size = f["data"].shape[0]
#         group.append(block_path)
#         group_size += block_size

#         if group_size >= max_group_size:
#             groups.append({
#                 "paths" : group,
#                 "size" : group_size,
#             })
#             group = []
#             group_size = 0
#     if group:
#         groups.append({
#             "paths" : group,
#             "size" : group_size,
#         })

#     return groups


# def get_training_block_nload(path, load_fraction):
#     with h5py.File(path) as f:
#         return int(load_fraction * f["data"].shape[0])


# def get_num_training_vecs():
#     args = get_retro_args()
#     block_paths = get_training_data_paths()
#     ntrain = sum(get_training_block_nload(p, args.retro_index_train_load_fraction)
# 		 for p in tqdm(block_paths, "ntrain"))
#     # pax({"block_paths": block_paths, "ntrain": ntrain})
#     return ntrain


# def load_training_block(path, load_fraction):
#     n_load = get_training_block_nload(path, load_fraction)
#     with h5py.File(path) as f:
#         return np.copy(f["data"][:n_load])


# def load_training_group(executor, group_info, load_fraction):

#     # Launch threads to load block data.
#     futures = []
#     for path in group_info["paths"]:
#         futures.append(executor.submit(load_training_block, path, load_fraction))

#     # Collect block data.
#     block_datas = []
#     for future in futures:
#         block_datas.append(future.result())

#     # Concatenate blocks.
#     group_data = np.concatenate(block_datas, axis=0)

#     # Garbage collect.
#     for d in block_datas:
#         del d
#     gc.collect()

#     return group_data


# def get_training_data_merged():
#     '''Merge embeddings into single dataset.'''

#     args = get_retro_args()

#     # Setup.
#     ds_infos = get_indexed_dataset_infos()
#     load_fraction = args.retro_index_train_load_fraction
#     n_chunks_sampled = sum(get_training_block_nload(p, load_fraction)
#                            for p in tqdm(get_training_data_paths(),
#                                          "count training samples"))

#     # Initialize merged data.
#     print("allocate training data array.")
#     t = time.time()
#     data = np.empty((n_chunks_sampled, args.retro_index_nfeats), dtype="f4")
#     print("  time : %.3f sec." % (time.time() - t))

#     # Data groups (minimizing fragmentation).
#     group_infos = get_training_data_group_infos()

#     # Load data blocks.
#     n_threads = max(len(group["paths"]) for group in group_infos)
#     with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as executor:

#         # Load data blocks.
#         print("load training data blocks.")
#         start_idx = 0
#         pbar = tqdm(group_infos)
#         for group_info in pbar:

#             pbar.set_description("mem %.0f gb, %.1f%%" % (
#                 psutil.virtual_memory()[3] / 1024**3,
#                 psutil.virtual_memory()[2],
#             ))

#             # Load group data.
#             group_data = load_training_group(executor, group_info, load_fraction)
#             data[start_idx:(start_idx+len(group_data))] = group_data
#             start_idx += len(group_data)

#             # Garbage collect.
#             del group_data
#             gc.collect()

#         # Verify num loaded.
#         assert start_idx == n_chunks_sampled

#         print("> training block data.shape = %s." % str(data.shape))

#     return data
# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
