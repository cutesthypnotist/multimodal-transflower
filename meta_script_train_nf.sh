#exp=transflower1
#./script_train_new.sh $exp --hparams_file=training/hparams/edf_gestures/${exp}.yaml --experiment_name ${exp}_edf_gestures3 --num_nodes 1 --max_epochs 50 --data_dir=./data/edf_extracted_data_rel/ --base_filenames_file base_filenames.txt $@
#./script_train_diffu.sh $exp --hparams_file=training/hparams/edf_gestures/${exp}.yaml --experiment_name ${exp}_edf_gestures2 --num_nodes 1 --max_epochs 80 --data_dir=./data/edf_extracted_data2/ --base_filenames_file base_filenames.txt $@

#exp=transflower_expmap_cr4_bs5_og2_futureN_gauss5_rel_single2
#exp=transflower1b
exp=transflower1c
#./script_train.sh $exp --hparams_file=training/hparams/neos_qb/${exp}.yaml --experiment_name ${exp}_quantum_bar_rel_nodp --num_nodes 1 --max_epochs 6000 --data_dir=data/quantum_bar_neosdata1_npy_relative --base_filenames_file base_filenames.txt
#./script_train.sh $exp --hparams_file=training/hparams/edf_gestures/${exp}.yaml --experiment_name ${exp}_edf_gestures --num_nodes 1 --max_epochs 6000 --data_dir=data/edf_extracted_data_rel/ --base_filenames_file base_filenames.txt $@
./script_train.sh $exp --hparams_file=training/hparams/edf_gestures/${exp}.yaml --experiment_name ${exp}_edf_gestures --num_nodes 1 --max_epochs 6000 --data_dir=data/edf_extracted_data_rel/ --base_filenames_file base_filenames.txt --continue_train --load_weights_only --learning_rate 2.5e-6