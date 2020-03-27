#!/usr/bin/env python
"""nasa_apod_desktop

Copyright (c) 2012 David Drake
 
Usage:

    nasa_apod_desktop -h
    nasa_apod_desktop -v
    nasa_apod_desktop [-d PATH]
                      [-f NAME]
                      [-r RES]
                      [-x RES_X]
                      [-y RES_Y]
                      [-u URI]
                      [-s]
                      [-d SEC]
                      [-c COUNT]
                      [-D]

Options:
    -d --download-path PATH     Where you want the file to be downloaded.
                                [Default: /tmp/backgrounds]
    -f --custom-folder NAME     If we detect your download folder, this will be the
                                target folder in there. [Default: nasa-apod-backgrounds]
    -r --resolution-type RES    One of the following three values:
                                    'stretch': single monitor or the combined resolution
                                               of your available monitors
                                    'largest': largest resolution of your available
                                               monitors
                                    'default': use the default resolution that is set
    -x --resolution-x RES_X     Horizontal resolution if RESOLUTION_TYPE is not default
                                or cannot be automatically determined.
    -y --resolution-y RES_Y     Vertical resolution if RESOLUTION_TYPE is not default or
                                cannot be automatically determined.
    -u --nasa-apod-site URI     Location of the current picture of the day.
                                [Default: http://apod.nasa.gov/apod/]
    -s --image-scroll           If present, will write also write an XML file to make the 
                                images scroll.
    -d --image-duration SEC     If IMAGE_SCROLL is enabled, this is the duration each will
                                stay in seconds.
    -c --seed-images COUNT      If > 0, it will download previous images as well to seed the
                                list of images. [Default: 10]
    -D --show-debug             Print useful debugging information or statuses
"""

import dataclasses
import enum
import glob
import os
import random
import re
import subprocess
import urllib
import urllib2
from datetime import datetime, timedelta
from sys import exit, stdout
from typing import Any, Dict, List, Optional

import docopt

import glib
from lxml import etree
from PIL import Image


class ResolutionTypes(enum.Enum):
    stretch = "stretch"
    largest = "largest"
    default = "default"


@dataclasses.dataclasses
class NASAADConfig:
    def __init__(self, opts: Dict[str, Any]):

        if "--download-path" in opts:
            self.DOWNLOAD_PATH = opts["--download-path"]
        if "--custom-folder" in opts:
            self.CUSTOM_FOLDER = opts["--custom-folder"]
        if "--resolution-type" in opts:
            self.RESOLUTION_TYPE = opts["--resolution-type"]
        if "--resolution-x" in opts:
            self.RESOLUTION_X = opts["--resolution-x"]
        if "--resolution-y" in opts:
            self.RESOLUTION_Y = opts["--resolution-y"]
        if "--nasa-apod-site" in opts:
            self.NASA_APOD_SITE = opts["--nasa-apod-site"]
        if "--image-scroll" in opts:
            self.IMAGE_SCROLL = opts["--image-scroll"]
        if "--image-duration" in opts:
            self.IMAGE_DURATION = opts["--image-duration"]
        if "--seed-images" in opts:
            self.SEED_IMAGES = opts["--seed-images"]
        if "--show-debug" in opts:
            self.SHOW_DEBUG = opts["--show-debug"]

    DOWNLOAD_PATH: str
    CUSTOM_FOLDER: str
    RESOLUTION_TYPE: ResolutionTypes
    RESOLUTION_X: int
    RESOLUTION_Y: int
    NASA_APOD_SITE: str
    IMAGE_SCROLL: bool
    IMAGE_DURATION: int
    SEED_IMAGES: int
    SHOW_DEBUG: bool


# Use XRandR to grab the desktop resolution. If the scaling method is set to 'largest',
# we will attempt to grab it from the largest connected device. If the scaling method
# is set to 'stretch' we will grab it from the current value. Default will simply use
# what was set for the default resolutions.
def find_resolution(
    res_type: ResolutionTypes, x: int, y: int, dbg: bool = False
) -> [int, int]:
    if res_type == ResolutionTypes.default:
        if dbg:
            print(f"Using default resolution of {x}x{y}")
        return x, y

    res_x = 0
    res_y = 0

    if dbg:
        print("Attempting to determine the current resolution.")
    if res_type == ResolutionTypes.largest:
        regex_search = "connected"
    else:
        regex_search = "current"

    p1 = subprocess.Popen(["xrandr"], stdout=subprocess.PIPE)
    p2 = subprocess.Popen(
        ["grep", regex_search], stdin=p1.stdout, stdout=subprocess.PIPE
    )
    p1.stdout.close()
    output = p2.communicate()[0]

    if res_type == ResolutionTypes.largest:
        # We are going to go through the connected devices and get the X/Y from the largest
        matches = re.finditer(" connected ([0-9]+)x([0-9]+)+", output)
        if matches:
            largest = 0
            for match in matches:
                if int(match.group(1)) * int(match.group(2)) > largest:
                    res_x = match.group(1)
                    res_y = match.group(2)
        elif dbg:
            print("Could not determine largest screen resolution.")
    else:
        reg = re.search(".* current (.*?) x (.*?),.*", output)
        if reg:
            res_x = reg.group(1)
            res_y = reg.group(2)
        elif dbg:
            print("Could not determine current screen resolution.")

    # If we couldn't find anything automatically use what was set for the defaults
    if res_x == 0 or res_y == 0:
        res_x = x
        res_y = y
        if dbg:
            print("Could not determine resolution automatically. Using defaults.")

    if dbg:
        print(f"Using detected resolution of {res_x}x{res_y}")

    return int(res_x), int(res_y)


# Uses GLib to find the localized "Downloads" folder
# See: http://askubuntu.com/questions/137896/how-to-get-the-user-downloads-folder-location-with-python
def set_download_folder(
    CUSTOM_FOLDER: str, DOWNLOAD_PATH: str, SHOW_DEBUG: bool = False
) -> str:
    downloads_dir = glib.get_user_special_dir(glib.USER_DIRECTORY_DOWNLOAD)
    if downloads_dir:
        # Add any custom folder
        new_path = os.path.join(downloads_dir, CUSTOM_FOLDER)
        if SHOW_DEBUG:
            print("Using automatically detected path: {new_path}")
    else:
        new_path = DOWNLOAD_PATH
        if SHOW_DEBUG:
            print("Could not determine download folder with GLib. Using default.")
    return new_path


# Download HTML of the site
def download_site(url: str, SHOW_DEBUG: bool = False):
    if SHOW_DEBUG:
        print("Downloading contents of the site to find the image name")
    opener = urllib2.build_opener()
    req = urllib2.Request(url)
    try:
        response = opener.open(req)
        reply = response.read()
    except urllib2.HTTPError as error:
        if SHOW_DEBUG:
            print("Error downloading {url} - {str(error.code)}")
        reply = f"Error: {str(error.code)}"
    return reply


# Finds the image URL and saves it
def get_image(
    text: str, DOWNLOAD_PATH: str, NASA_APOD_SITE: str, SHOW_DEBUG: bool = False
):
    if SHOW_DEBUG:
        print("Grabbing the image URL")
    file_url, filename, file_size = get_image_info(
        "a href", text, NASA_APOD_SITE, SHOW_DEBUG
    )
    # If file_url is None, the today's picture might be a video
    if file_url is None:
        return None

    if SHOW_DEBUG:
        print("Found name of image:{filename}")

    save_to = os.path.join(DOWNLOAD_PATH, os.path.splitext(filename)[0] + ".png")

    if not os.path.isfile(save_to):
        # If the response body is less than 500 bytes, something went wrong
        if file_size < 500:
            print("Response less than 500 bytes, probably an error")
            print("Attempting to just grab image source")
            file_url, filename, file_size = get_image_info(
                "img src", text, NASA_APOD_SITE, SHOW_DEBUG
            )
            # If file_url is None, the today's picture might be a video
            if file_url is None:
                return None
            print(f"Found name of image:{filename}")
            if file_size < 500:
                # Give up
                if SHOW_DEBUG:
                    print("Could not find image to download")
                exit()

        if SHOW_DEBUG:
            print("Retrieving image")
            urllib.urlretrieve(file_url, save_to, print_download_status)

            # Adding additional padding to ensure entire line
            if SHOW_DEBUG:
                print(f"\rDone downloading {human_readable_size(file_size)}       ")
        else:
            urllib.urlretrieve(file_url, save_to)
    elif SHOW_DEBUG:
        print("File exists, moving on")

    return save_to


def resize_image(filename, RESOLUTION_X: int, RESOLUTION_Y: int, SHOW_DEBUG: bool):
    """Resizes the image to the provided dimensions."""
    if SHOW_DEBUG:
        print("Opening local image")

    image = Image.open(filename)
    current_x, current_y = image.size
    if (current_x, current_y) == (RESOLUTION_X, RESOLUTION_Y):
        if SHOW_DEBUG:
            print("Images are currently equal in size. No need to scale.")
    else:
        if SHOW_DEBUG:
            print(
                f"Resizing the image from {image.size[0]}x{image.size[1]} to {RESOLUTION_X}x{RESOLUTION_Y}"
            )
        image = image.resize((RESOLUTION_X, RESOLUTION_Y), Image.ANTIALIAS)

        if SHOW_DEBUG:
            print(f"Saving the image to '{filename}'")

        with open(filename, "w") as fhandle:
            image.save(fhandle, "PNG")


# Sets the new image as the wallpaper
def set_gnome_wallpaper(file_path, SHOW_DEBUG: bool = False):
    if SHOW_DEBUG:
        print("Setting the wallpaper")
    command = (
        f"gsettings set org.gnome.desktop.background picture-uri file://{file_path}"
    )
    subprocess.Popen(command, shell=True)

    # status, output = commands.getstatusoutput(command)
    # return status


def print_download_status(block_count, block_size, total_size):
    written_size = human_readable_size(block_count * block_size)
    total_size = human_readable_size(total_size)

    # Adding space padding at the end to ensure we overwrite the whole line
    stdout.write("\r%s bytes of %s         " % (written_size, total_size))
    stdout.flush()


def human_readable_size(number_bytes):
    for x in ["bytes", "KB", "MB"]:
        if number_bytes < 1024.0:
            return "%3.2f%s" % (number_bytes, x)
        number_bytes /= 1024.0


# Creates the necessary XML so background images will scroll through
def create_desktop_background_scoll(
    filename,
    IMAGE_SCROLL: bool,
    IMAGE_DURATION: int,
    DOWNLOAD_PATH: str,
    SEED_IMAGES: int,
    NASA_APOD_SITE: str,
    RESOLUTION_X: int,
    RESOLUTION_Y: int,
    SHOW_DEBUG: bool = False,
):
    if not IMAGE_SCROLL:
        return filename

    if SHOW_DEBUG:
        print("Creating XML file for desktop background switching.")

    filename = f"{DOWNLOAD_PATH}/nasa_apod_desktop_backgrounds.xml"

    # Create our base, background element
    background = etree.Element("background")

    # Grab our PNGs we have downloaded
    images = glob.glob(f"{DOWNLOAD_PATH}/*.png")
    num_images = len(images)

    if num_images < SEED_IMAGES:
        # Let's seed some images
        # Start with yesterday and continue going back until we have enough
        if SHOW_DEBUG:
            print("Downloading some seed images as well")
        days_back = 0
        seed_images_left = SEED_IMAGES
        while seed_images_left > 0:
            days_back += 1
            if SHOW_DEBUG:
                print(f"Downloading seed image ({str(seed_images_left)} left):")
            day_to_try = datetime.now() - timedelta(days=days_back)

            # Filenames look like /apYYMMDD.html
            seed_filename = f"{NASA_APOD_SITE}ap{day_to_try.strftime('%y%m%d')}.html"
            seed_site_contents = download_site(seed_filename, SHOW_DEBUG)

            # Make sure we didn't encounter an error for some reason
            if seed_site_contents == "error":
                continue

            seed_filename = get_image(seed_site_contents)
            # If the content was an video or some other error occurred, skip the
            # rest.
            if seed_filename is None:
                continue

            resize_image(seed_filename, RESOLUTION_X, RESOLUTION_Y, SHOW_DEBUG)

            # Add this to our list of images
            images.append(seed_filename)
            seed_images_left -= 1
        if SHOW_DEBUG:
            print("Done downloading seed images")

    # Get our images in a random order so we get a new order every time we get a new file
    random.shuffle(images)
    # Recalculate the number of pictures
    num_images = len(images)

    for i, image in enumerate(images):
        # Create a static entry for keeping this image here for IMAGE_DURATION
        static = etree.SubElement(background, "static")

        # Length of time the background stays
        duration = etree.SubElement(static, "duration")
        duration.text = str(IMAGE_DURATION)

        # Assign the name of the file for our static entry
        static_file = etree.SubElement(static, "file")
        static_file.text = images[i]

        # Create a transition for the animation with a from and to
        transition = etree.SubElement(background, "transition")

        # Length of time for the switch animation
        transition_duration = etree.SubElement(transition, "duration")
        transition_duration.text = "5"

        # We are always transitioning from the current file
        transition_from = etree.SubElement(transition, "from")
        transition_from.text = images[i]

        # Create our tranition to element
        transition_to = etree.SubElement(transition, "to")

        # Check to see if we're at the end, if we are use the first image as the image to
        if i + 1 == num_images:
            transition_to.text = images[0]
        else:
            transition_to.text = images[i + 1]

    xml_tree = etree.ElementTree(background)
    xml_tree.write(filename, pretty_print=True)

    return filename


def get_image_info(
    element: str, text: str, NASA_APOD_SITE: str, SHOW_DEBUG: bool
) -> [Optional[str], Optional[str], Optional[str]]:
    """Grabs information about the image."""
    regex = "<" + element + '="(image.*?)"'
    reg = re.search(regex, text, re.IGNORECASE)
    if reg:
        if "http" in reg.group(1):
            # Actual URL
            file_url = reg.group(1)
        else:
            # Relative path, handle it
            file_url = f"{NASA_APOD_SITE}{reg.group(1)}"
    else:
        if SHOW_DEBUG:
            print("Could not find an image. May be a video today.")
        return None, None, None

    # Create our handle for our remote file
    if SHOW_DEBUG:
        print("Opening remote URL")

    remote_file = urllib.urlopen(file_url)

    filename = os.path.basename(file_url)
    file_size = float(remote_file.headers.get("content-length"))

    return file_url, filename, file_size


def main(opts: NASAADConfig):
    # Our program
    if opts.SHOW_DEBUG:
        print("Starting")

    # Find desktop resolution
    opts.RESOLUTION_X, opts.RESOLUTION_Y = find_resolution(
        opts.RESOLUTION_TYPE, opts.RESOLUTION_X, opts.RESOLUTION_Y, opts.SHOW_DEBUG
    )

    # Set a localized download folder
    opts.DOWNLOAD_PATH = set_download_folder(
        opts.CUSTOM_FOLDER, opts.DOWNLOAD_PATH, opts.SHOW_DEBUG
    )

    # Create the download path if it doesn't exist
    if not os.path.exists(os.path.expanduser(opts.DOWNLOAD_PATH)):
        os.makedirs(os.path.expanduser(opts.DOWNLOAD_PATH))

    # Grab the HTML contents of the file
    site_contents = download_site(opts.NASA_APOD_SITE, opts.SHOW_DEBUG)
    if site_contents == "error":
        if opts.SHOW_DEBUG:
            print("Could not contact site.")
        exit()

    # Download the image
    filename = get_image(
        site_contents, opts.DOWNLOAD_PATH, opts.NASA_APOD_SITE, opts.SHOW_DEBUG
    )
    if filename is not None:
        # Resize the image
        resize_image(filename, opts.RESOLUTION_X, opts.RESOLUTION_Y, opts.SHOW_DEBUG)

    # Create the desktop switching xml
    filename = create_desktop_background_scoll(
        filename,
        opts.IMAGE_SCROLL,
        opts.IMAGE_DURATION,
        opts.DOWNLOAD_PATH,
        opts.SEED_IMAGES,
        opts.NASA_APOD_SITE,
        opts.RESOLUTION_X,
        opts.RESOLUTION_Y,
        opts.SHOW_DEBUG,
    )

    # If the script was unable todays image and IMAGE_SCROLL is set to False,
    # the script exits
    if filename is None:
        if opts.SHOW_DEBUG:
            print("Today's image could not be downloaded.")
        exit()

    # Set the wallpaper
    status = set_gnome_wallpaper(filename)
    if opts.SHOW_DEBUG:
        print("Finished!")


if __name__ == "__main__":
    config = NASAADConfig(docopt.docopt(__doc__, version="1.0.1"))
    main(opts=config)
