"""train.py

This is the train main function that call creation of training set of a dataset
and train the models.
"""

import os
from pathlib import Path
from os.path import join, exists
from collections import defaultdict
from utils import create_logging, set_device, set_environment_paths, TrainArgs
from parser import get_parser, get_processed_args
from net_utils.utils import unique_path
from net_utils import unets
from training.create_training_sets import create_ctc_training_sets, get_file
from training.training import train, train_auto, get_max_epochs, get_weights
from training.mytransforms import augmentors
from training.autoencoder_dataset import AutoEncoderDataset
from training.cell_segmentation_dataset import CellSegDataset


def main():
    """ Main function to set up paths, datasets and training pipelines
    """
    log = create_logging() # Set up 'logger' object 

    args = get_parser() # Set up dict arguments
    args = get_processed_args(args)

    env = {} # TODO: Load this from a '.json' file environment parameters
    env['logger'] = log # Move the object through 'env' dict

    log.info(f"Args: {args}") # Print overall args 
    log.debug(f"Env varibles: {env}")
    device, num_gpus = set_device() # Set device: cpu or single-gpu usage
    set_environment_paths()
    log.info(f">>>   Training: pre-processing {args.pre_processing_pipeline} model {args.model_pipeline} <<<")

    # Load paths
    path_data = Path(args.train_images_path)
    path_models = args.models_folder # Train all models found here.
    cell_type = Path(args.dataset)

    # TODO: Move this into 'utils' file (called both in 'val' and 'train')
    if not exists(path_data):
        log.info(f"Warning: the '{path_data}' provided is not existent! Interrupting the program...")
        raise ValueError("The '{path_data}' provided is not existent")
    else:
        trainset_name = args.dataset # 'args.dataset' used as cell type

    # Pre-processing pipeline - implement more pipeline from other papers here ..
    if args.pre_processing_pipeline == 'kit-ge':
        log.info(f"Creation of the training dataset using {args.pre_processing_pipeline} pipeline for {args.crop_size} crops")
        
        for crop_size in args.crop_size: # If you want to create more dataset
            create_ctc_training_sets(log, path_data=path_data, mode=args.mode, cell_type=cell_type, split=args.split, min_a_images=args.min_a_images, crop_size = crop_size)
    else:
        raise ValueError("This argument support just 'kit-ge' as pre-processing pipeline")

    # If it is desired to just create the training set
    if args.train_loop == False:
        log.info(f">>> Creation of the trainining dataset scripts ended correctly <<<")
        return None # Exit the script

    # Get training settings - As in 'eval.py', the args for training are split in a specific parser for readibility.
    train_args = TrainArgs(model_pipeline = args.model_pipeline,
                            act_fun = args.act_fun,
                            batch_size = args.batch_size, 
                            filters = args.filters,
                            iterations = args.iterations,
                            loss = args.loss,
                            norm_method = args.norm_method,
                            optimizer = args.optimizer,
                            pool_method = args.pool_method,
                            pre_train = args.pre_train,
                            retrain = args.retrain,
                            split = args.split)

    # Training parameters used for all the iterations/crop options given                         
    log.info(f"Training parameters {train_args}")

    # Parsing the configurations - get CNN (double encoder U-Net). WARNING: Double 'decoder', not encoder.
    train_configs = {'architecture': ("DU", train_args.pool_method, train_args.act_fun, train_args.norm_method, train_args.filters),
                    'batch_size': train_args.batch_size,
                    'batch_size_auto': 2,
                    'label_type': "distance",
                    'loss': train_args.loss,
                    'num_gpus': num_gpus,
                    'optimizer': train_args.optimizer
                    }

    # Building the architecture that will be used for every 
    net = unets.build_unet(unet_type=train_configs['architecture'][0],
                        act_fun=train_configs['architecture'][2],
                        pool_method=train_configs['architecture'][1],
                        normalization=train_configs['architecture'][3],
                        device=device,
                        num_gpus=num_gpus,
                        ch_in=1,
                        ch_out=1,
                        filters=train_configs['architecture'][4])




    for idx, crop_size in enumerate(args.crop_size): # Cicle over multiple 'crop_size' if provided
        model_name = '{}_{}_{}_{}_model'.format(trainset_name, args.mode, args.split, args.crop_size)
        log.info(f"{idx} Model used is {model_name}")

        # Train multiple models
        for i in range(args.iterations):

            run_name = unique_path(path_models, model_name + '_{:02d}.pth').stem
            
            # Update the configurations
            train_configs['run_name']=run_name

            if args.pre_train and args.retrain:
                raise Exception('Use either the pre-train option --pre_train or the retrain option --retrain')

            if args.retrain:
                old_model = Path(__file__).parent / args.retrain
                if get_file(old_model.parent / "{}.json".format(old_model.stem))['architecture'][-1] != train_configs['architecture'][-1]:
                    raise Exception('Architecture of model to retrain does not match.')
                # Get weights of trained model to retrain
                print("Load models of {}".format(old_model.stem))
                net = get_weights(net=net, weights=str('{}.pth'.format(old_model)), num_gpus=num_gpus, device=device)
                train_configs['retrain_model'] = old_model.stem

            # Pre-training of the Encoder in autoencoder style
            train_configs['pre_trained'] = False
            if args.pre_train:

                if args.mode != 'GT' or len(args.cell_type) > 1:
                    raise Exception('Pre-training only for GTs and for single cell type!')

                # Get CNN (U-Net without skip connections)
                net_auto = unets.build_unet(unet_type='AutoU',
                                            act_fun=train_configs['architecture'][2],
                                            pool_method=train_configs['architecture'][1],
                                            normalization=train_configs['architecture'][3],
                                            device=device,
                                            num_gpus=num_gpus,
                                            ch_in=1,
                                            ch_out=1,
                                            filters=train_configs['architecture'][4])

                # Load training and validation set
                data_transforms_auto = augmentors(label_type='auto', min_value=0, max_value=65535)
                datasets = AutoEncoderDataset(data_dir=path_data / args.cell_type[0],
                                          train_dir=path_data / "{}_{}_{}_{}".format(trainset_name, args.mode, args.split, crop_size),
                                          transform=data_transforms_auto)

                
                # Train model
                train_auto(net=net_auto, dataset=datasets, configs=train_configs, device=device,  path_models=path_models)

                # Load best weights and load best weights into encoder before the fine-tuning.
                net_auto = get_weights(net=net_auto, weights=str(path_models / '{}.pth'.format(run_name)), num_gpus=num_gpus, device=device)
                pretrained_dict, net_dict = net_auto.state_dict(), net.state_dict()
                pretrained_dict = {k: v for k, v in pretrained_dict.items() if k in net_dict}  # 1. filter unnecessary keys
                net_dict.update(pretrained_dict)  # 2. overwrite entries
                net.load_state_dict(net_dict)  # 3. load the new state dict
                train_configs['pre_trained'] = True
                del net_auto
            
            # Load training and validation set - this is the train (or fine-tuning after the pre-training phase).
            data_transforms = augmentors(label_type=train_configs['label_type'], min_value=0, max_value=65535)
            train_configs['data_transforms'] = str(data_transforms)
            dataset_name = "{}_{}_{}_{}".format(trainset_name, args.mode, args.split, crop_size)

            # WORK IN PROGRESS !!!
            
            # In the original script it was implemented the 'all' dataset plus ST option.
            datasets = {x: CellSegDataset(root_dir=path_data / dataset_name, mode=x, transform=data_transforms[x])
                        for x in ['train', 'val']}


    log.info(">>> Training script ended correctly <<<")


# Implementing 'kit-ge' training method - consider to make unique for every chosen pipeline/make modular later.
def kit_ge_model_pipeline(log, models, path_models, train_sets, path_data, device, scale_factor, args):
    pass


if __name__ == "__main__":
    main()