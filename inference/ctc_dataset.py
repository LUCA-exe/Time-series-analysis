'''
This script helps the creation of the inference (evaluation/test) dataset.
'''
import numpy as np
import tifffile as tiff
import torch
from pathlib import Path
from logging import Logger
from skimage.exposure import equalize_adapthist
from skimage.transform import rescale
from torch.utils.data import Dataset
from torchvision import transforms
import os

from net_utils.utils import zero_pad_model_input, save_image

class CTCDataSet(Dataset):
    """Custom dataset for Cell Tracking Challenge format data.
    """

    def __init__(self, data_dir: str, 
                 transform: transforms.Compose = lambda x: x, 
                 is_test_sample = False):
        """

        Args:
            data_dir: Directory with the Cell Tracking Challenge images to predict.
            transform: List of class for image processing.
            is_test_sample = Flag to set if it called during a system or performance
            unit test. It will reduce the image dimensionality.
        """
        self.img_ids = sorted(Path(data_dir).glob('t*.tif'))
        self.transform = transform
        self.is_test_sample = is_test_sample

    def __len__(self) -> int:
        return len(self.img_ids)

    def __getitem__(self, idx: int): 
        """This method will return a sample dict. containing the different
        channel agggreagations of a single image.

        Specifically, the EVs and Nuclei are respectively the red (0) and blue(2)
        channels. 
        The normal image instead is an aggregatation of all channels.
        """

        img_id = self.img_ids[idx]
        image = tiff.imread(str(img_id))  

        if self.is_test_sample:
            image = image[:120, :120, :]

        # NOTE: Processing in case of RGB images - tailored for my dataset.
        if len(image.shape) > 2:
            single_channel_img = image[:, :, 0]
            nuclei_channel_img = image[:, :, 2]

            # NOTE: Keep all channels.
            img = np.sum(image, axis=2)  
        else:
            # NOTE: Temporary workaround.
            single_channel_img = image
            img = image

        sample = {'image': img,
                'single_channel_image': single_channel_img,
                'nuclei_channel_image': nuclei_channel_img,
                'id': img_id.stem}
        sample = self.transform(sample)
        return sample


def pre_processing_transforms(apply_clahe, scale_factor):
    """ Get transforms for the CTC data set.

    :param apply_clahe: apply CLAHE.
        :type apply_clahe: bool
    :param scale_factor: Downscaling factor <= 1.
        :type scale_factor: float

    :return: transforms
    """
    data_transforms = transforms.Compose([ContrastEnhancement(apply_clahe),
                                          Normalization(),
                                          Scaling(scale_factor),
                                          Padding(),
                                          ToTensor()])

    return data_transforms


class ContrastEnhancement(object):

    def __init__(self, apply_clahe):
        self.apply_clahe = apply_clahe

    def __call__(self, sample):

        if self.apply_clahe:
            img = sample['image']
            img = equalize_adapthist(np.squeeze(img), clip_limit=0.01)
            img = (65535 * img).astype(np.uint16)
            sample['image'] = img

            # TODO: refactor the tranformation in a private function of this objetc
            if not sample["single_channel_image"] is None:
                img = sample["single_channel_image"]
                img = equalize_adapthist(np.squeeze(img), clip_limit=0.01)
                img = (65535 * img).astype(np.uint16)
                sample["single_channel_image"] = img

                img = sample["nuclei_channel_image"]
                img = equalize_adapthist(np.squeeze(img), clip_limit=0.01)
                img = (65535 * img).astype(np.uint16)
                sample["nuclei_channel_image"] = img
        return sample


class Normalization(object):

    def __call__(self, sample):

        img = sample['image']
        img = 2 * (img.astype(np.float32) - img.min()) / (img.max() - img.min()) - 1
        sample['image'] = img

        # TODO: refactor the tranformation in a private function of this objetc
        if not sample["single_channel_image"] is None:
            img = sample["single_channel_image"]
            img = 2 * (img.astype(np.float32) - img.min()) / (img.max() - img.min()) - 1
            sample["single_channel_image"] = img

            img = sample["nuclei_channel_image"]
            img = 2 * (img.astype(np.float32) - img.min()) / (img.max() - img.min()) - 1
            sample["nuclei_channel_image"] = img
        return sample


class Padding(object):

    def __call__(self, sample):

        img = sample['image']
        img, pads = zero_pad_model_input(img=img, pad_val=np.min(img))
        sample['image'] = img
        sample['pads'] = pads

        # TODO: refactor the tranformation in a private function of this objetc
        if not sample["single_channel_image"] is None:
            img = sample["single_channel_image"]
            img, _ = zero_pad_model_input(img=img, pad_val=np.min(img))
            sample["single_channel_image"] = img

            img = sample["nuclei_channel_image"]
            img, _ = zero_pad_model_input(img=img, pad_val=np.min(img))
            sample["nuclei_channel_image"] = img
        return sample


class Scaling(object):

    def __init__(self, scale):
        self.scale = scale

    def __call__(self, sample):

        img = sample['image']
        sample['original_size'] = img.shape

        if self.scale < 1:

            if len(img.shape) == 3:
                img = rescale(img, (1, self.scale, self.scale), order=2, preserve_range=True).astype(img.dtype)
            else:
                img = rescale(img, (self.scale, self.scale), order=2, preserve_range=True).astype(img.dtype)
                
                # Additional processing on the single channel if not None
                if not sample['single_channel_image'] is None:
                    single_channel_img = sample['single_channel_image']
                    single_channel_img = rescale(single_channel_img, (self.scale, self.scale), order=2, preserve_range=True).astype(single_channel_img.dtype)
                    sample['single_channel_image'] = single_channel_img

                    single_channel_img = sample['nuclei_channel_image']
                    single_channel_img = rescale(single_channel_img, (self.scale, self.scale), order=2, preserve_range=True).astype(single_channel_img.dtype)
                    sample['nuclei_channel_image'] = single_channel_img
            sample['image'] = img
        return sample


class ToTensor(object):
    """ Convert image and label image to Torch tensors """

    def __call__(self, sample):

        img = sample['image']
        if len(img.shape) == 2:

            img = img[None, :, :]
            if not sample["single_channel_image"] is None:

                single_channel_image = sample["single_channel_image"]
                single_channel_image = single_channel_image[None, :, :]
                single_channel_image = torch.from_numpy(single_channel_image).to(torch.float)
                sample["single_channel_image"] = single_channel_image

                single_channel_image = sample["nuclei_channel_image"]
                single_channel_image = single_channel_image[None, :, :]
                single_channel_image = torch.from_numpy(single_channel_image).to(torch.float)
                sample["nuclei_channel_image"] = single_channel_image
        img = torch.from_numpy(img).to(torch.float)
        sample["image"] = img
        return sample # Directly return the dict. of the batches
    
### Utils functions ###

def show_inference_dataset_samples(log: Logger, dataset: CTCDataSet, samples: int = 2):
    """Visual debug for the images used in the inference phase.
    """

    log.debug(f"Visually inspect the first {samples} samples of images from the inference CTC Dataset")
    folder = os.getenv("TEMPORARY_PATH")
    for idx in range(samples):

        image_dict = dataset[idx]
        for pos, (key, image) in enumerate(image_dict.items()):

            if key in ["image", "single_channel_image", "nuclei_channel_image"]:
                curr_title = "Sample " + str(idx) + f" type ({key})"
                save_image(np.squeeze(image), folder, curr_title, use_cmap=True)
    log.debug(f"Images correctly saved in {folder} before the inference phase!")
    return True