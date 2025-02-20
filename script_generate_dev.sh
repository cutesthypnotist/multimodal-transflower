#!/bin/bash

#if using XLA
export XRT_WORKERS="localservice:0;grpc://localhost:40934"
export XRT_DEVICE_MAP="CPU:0;/job:localservice/replica:0/task:0/device:XLA_CPU:0|GPU:0;/job:localservice/replica:0/task:0/device:XLA_GPU:0"

py=python3

#exp=transglower_residual_aistpp_expmap
#exp=moglow_aistpp_expmap
#exp=testing
#exp=moglow_expmap1_label_newdata2
exp=$1
#exp=transflower_expmap_old
#exp=mowgli_expmap_stage2_newdata
#exp=$1
#exp=mowgli_aistpp_expmap_future3
#exp=aistpp_residual
#seq_id=gKR_sFM_cAll_d28_mKR5_ch06
#seq_id=gLH_sFM_cAll_d16_mLH3_ch04
#seq_id=gPO_sFM_cAll_d12_mPO4_ch19
#seq_id=aistpp_gMH_sFM_cAll_d22_mMH3_ch04
#seq_id=data_kulzaworld_guille_neosdata_U_Kulza_R_57ea6247_a178_45c5_a3bb_a95af490bfb0_S-898a7978-79fa-4fd0-8f4d-e7cfb8a1e397_a06ffd39-1343-4854-8d2f-225156c7cf5d_2_ID2C00_streams
#seq_id=groovenet_2
seq_id=UR5_Tianwei_obs_act_etc_0_data
echo $exp $seq_id

mkdir inference/generated/
mkdir inference/generated/${exp}
mkdir inference/generated/${exp}/predicted_mods
mkdir inference/generated/${exp}/videos
#fps=20
#fps=60
#root_dir=$SCRATCH/data
root_dir=data
#data_dir=data/aistpp_20hz
#data_dir=data/dance_combined3
#data_dir=data/kulzaworld_guille_neosdata_npy
#data_dir=${root_dir}/kulzaworld_guille_neosdata_npy_relative
data_dir=${root_dir}/UR5_processed

#data_dir=$SCRATCH/data/dance_combined3
#data_dir=data/aistpp_60hz

# if we don't pass seq_id it will choose a random one from the test set
$py inference/generate.py --data_dir=$data_dir --output_folder=inference/generated --experiment_name=$exp \
    --seq_id $seq_id \
    --max_length 300 \
    --save_jit \
    --use_temperature
    #--generate_video \


