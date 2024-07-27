# earthacrosstime

*Mastodon/Twitter bot that posts videos showcasing how random locations in the world have changed since 1984.*

In a bit more detail, whenever the bot runs, it...

* **loads a [shapefile](https://en.wikipedia.org/wiki/Shapefile)** from disk,
* generates a **random point** within the bounds of this shape,
* figures out **which video tile needs to be downloaded** to the point and an area around it,
* **downloads that video** from the repository underlying [Google Earth Timelapse](https://earthengine.google.com/timelapse/),
* **reverse geocodes** the chosen point using [Nominatim](https://nominatim.openstreetmap.org/ui/reverse.html) to figure out the location's name,
* **edits** the video, annotating it with **latitude & longitude, area covered, and a named pin on a world map**,
* **saves** that to disk,
* and **tweets and/or toots** the edited video, optionally with a geotag.

Much of the code has been adapted from [ærialbot](https://github.com/doersino/aerialbot), a previous project of mine that basically does the same (and more!) for static maps, and [CMU's Time Machine Viewer](https://github.com/CMU-CREATE-Lab/timemachine-viewer), which contains reference implementations of the required coordinate projections.

#### 🐦 Check it out at [@aerialbot@botsin.space](https://botsin.space/@aerialbot), where every sixth post is made by this bot!

Here's one of the videos posted by this bot, showing the construction of [Incheon Airport](https://en.wikipedia.org/wiki/Incheon_International_Airport) and various developments on land.

https://user-images.githubusercontent.com/1944410/120917015-35d93000-c6ad-11eb-9ab8-6d1d3b7a5525.mp4


## Features

Here's why this bot is a [Good Bot](https://www.reddit.com/r/OutOfTheLoop/comments/6oca11/what_is_up_with_good_bot_bad_bot_comments/):

* **Configurability:** Take a look at `config.sample.ini` – you can supply your own shapefile (or instead define a fixed point), control output verbosity, set a different Time Machine repository, scale the result videos to your preferred size, define the text of the tweet, and more!
* **Correctness:** Because neighboring meridians are closer at the poles than at the equator, uniformly sampling the allowable range of latitudes would bias the generated random points toward the poles. Instead, this bot makes sure they are distributed with regard to surface area.
* **Automatic zoom level selection:** Simply supply a maximum allowable number of meters per pixel – the code will then take care of dialing in a (more than) sufficient zoom level.
* **Comes with batteries included:** The `assets/world-shapefile/` directory contains a shapefile providing the outlines of the continents and most islands. More shapefiles, along with a guide on how to convert third-party shapefiles to the correct format, can be found [here](https://github.com/doersino/aerialbot/tree/master/shapefiles).
* **Cares about typography:** The text that's superimposed onto the result videos is aligned with utmost precision. Just in case you were wondering.
* **Geotagging:** Tweets will be geotagged with the exact location – you can disable this, of course.
* **Logging:** Keeps a log file – whether that's for debugging or reminiscing is your call. Again, you can disable this easily.


## Usage

### Setup

Being a good [Python 3](https://www.python.org) citizen, this program integrates with `venv` or similar packages to avoid dependency hell. Run the following commands to get it installed on your system:

```bash
$ git clone https://github.com/doersino/earthacrosstime
$ python3 -m venv earthacrosstime
$ cd earthacrosstime
$ source bin/activate
$ pip3 install -r requirements.txt
```

(To deactivate the virtual environment, run `deactivate`.)

One of the dependencies, [Shapely](https://shapely.readthedocs.io/en/stable/manual.html), requires the [GEOS library](https://github.com/libgeos/geos) for performing operations on two-dimensional vector geometries, which you *may* need to install first as described [here](https://stackoverflow.com/questions/19742406/could-not-find-library-geos-c-or-load-any-of-its-variants).


### Configuration

Copy `config.sample.ini` to `config.ini`, open it and modify it based on the (admittedly wordy) instructions in the comments.

See [here](https://github.com/doersino/aerialbot/tree/master/shapefiles) for advice regarding finding shapefiles of the region you're interested in and preparing them for use with ærialbot.


### Running

Once you've set everything up and configured it to your liking, navigate to the directory where `earthacrosstime.py` is located (this is important – the bot won't be able to find some required assets otherwise) and run it:

```bash
$ python3 earthacrosstime.py
```

That's basically it!

If you want your bot to post at predefined intervals, use `cron`, [`runwhen`](http://code.dogmap.org/runwhen/) or a similar tool. To make `cron` work with `venv`, you'll need to use bash and execute the `activate` script before running the bot (in this example, it runs every eight hours at 50 past the hour):

```
50 */8 * * * /usr/bin/env bash -c 'cd /PATH/TO/earthacrosstime && source bin/activate && python3 earthacrosstime.py'
```

*Pro tip:* If you want to host multiple instances of this bot, you don't need multiple copies of the code – multiple config files suffice: simply run `python3 earthacrosstime.py one-of-your-config-files.ini`.

*Uber pro tip:* Run `python3 earthacrosstime.py --help` to learn about some secret CLI options!


## License

You may use this repository's contents under the terms of the *MIT License*, see `LICENSE`.

However, the subdirectory `assets/` contains some **third-party software and data with their own licenses**:

* [Optician Sans](https://optician-sans.com/), the font used in the result videos and located at `assets/optician-sans.otf`, is licensed under the *SIL Open Font License*, see `assets/optician-sans-README.txt` or [here](https://opensource.org/licenses/OFL-1.1).

* The included shapefile, located at `assets/world-shapefile/`, was [created by Carlos Efraín Porto Tapiquén](https://tapiquen-sig.jimdofree.com/english-version/free-downloads/world/), who mandates the following attribution: "Shape downloaded from http://tapiquen-sig.jimdo.com. Carlos Efraín Porto Tapiquén. Orogénesis Soluciones Geográficas. Porlamar, Venezuela, 2015."

* The world map displayed at the end of the generated videos, located at `assets/map.png`, is based on a [PNG render](https://upload.wikimedia.org/wikipedia/commons/thumb/d/df/World_location_map_mono.svg/3840px-World_location_map_mono.svg.png) of a [SVG map uploaded to Wikimedia Commons by user SharkD](https://commons.wikimedia.org/wiki/File:World_location_map_mono.svg) who has released it into the public domain.
