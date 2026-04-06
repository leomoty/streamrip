# streamrip

A scriptable stream downloader for Qobuz.

> [!NOTE]
> This is a fork of Streamrip 1.9.7 with Qobuz' authentication backported from Streamrip 2.x.
> 
> Support for Deezer, Tidal, Soundcloud and Last.FM has been removed.

## Features

- Super fast, as it utilizes concurrent downloads and conversion
- Downloads tracks, albums, playlists, discographies, and labels from Qobuz
- Automatically converts files to a preferred format
- Has a database that stores the downloaded tracks' IDs so that repeats are avoided
- Easy to customize with the config file

## Installation

First, ensure [Python](https://www.python.org/downloads/) (version 3.8 or greater) and [pip](https://pip.pypa.io/en/stable/installing/) are installed. If you are on Windows, install [Microsoft Visual C++ Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/). Then run the following in the command line:

```bash
pip3 install streamrip --upgrade
```

If you would like to use `streamrip`'s conversion capabilities, install [ffmpeg](https://ffmpeg.org/download.html).

### Streamrip beta

If you want to get access to the latest and greatest features without waiting for a new release, install
from the `dev` branch with the following command

```bash
pip3 install git+https://github.com/leomoty/streamrip.git@dev
```

## Authentication

> [!IMPORTANT]
> Note: Due to recent changes to Qobuz authentication and infrastructure, it's no longer possible to authenticate via the now deprecated email+password and requires you to manually fetch the Qobuz User ID and `user_auth_token` from a browser with an authenticated Qobuz session.

To grab these values follow these steps:
1) Open a browser and navigate to play.qobuz.com and login.
2) Open the Web Developer Tools and the network tab.
3) Reload the page and filter for "login", now check the response and look for `id` (this numeric string is your Qobuz user ID), and `user_auth_token`.
4) Open the Streamrip config by running `rip config --open`, find `[qobuz]` and add the `id` as `email_or_userid` and the `user_auth_token` as the `password_or_token`.

This token will at expire at some point, when this happens, grab a new one and repeat the steps above.


## Example Usage

**You need a subscription to use this tool with Qobuz.**

Download an album from Qobuz

```bash
rip url https://play.qobuz.com/album/5099969390852
```

Download multiple albums from Qobuz

```bash
rip url https://play.qobuz.com/album/5099969390852 https://play.qobuz.com/album/r8he7yd5j7xsb
```



To set the maximum quality, use the `--max-quality` option to `1, 2, 3, 4`:

| Quality ID | Audio Quality         | Available Sources                            |
| ---------- | --------------------- | -------------------------------------------- |
| 1          | 320 kbps MP3          | Qobuz                                        |
| 2          | 16 bit, 44.1 kHz (CD) | Qobuz                                        |
| 3          | 24 bit, ≤ 96 kHz      | Qobuz                                        |
| 4          | 24 bit, ≤ 192 kHz     | Qobuz                                        |



```bash
rip url --max-quality 3 https://play.qobuz.com/album/5099969390852
```

Want to find some new music? Use the `discover` command

```bash
rip discover
```

For additional customization, see the config file

```
rip config --open
```



If you're confused about anything, see the help pages. The main help pages can be accessed by typing `rip` by itself in the command line. The help pages for each command can be accessed with the `-h` flag. For example, to see the help page for the `url` command, type

```
rip url -h
```

## Acknowledgements

Streamrip was originally created by Nathom.

Thanks to Vitiko98, Sorrow446, and DashLt for their contributions to this project, and the previous projects that made this one possible.

`streamrip` was inspired by:

- [qobuz-dl](https://github.com/vitiko98/qobuz-dl)
- [Qo-DL Reborn](https://github.com/badumbass/Qo-DL-Reborn)
- [Tidal-Media-Downloader](https://github.com/yaronzz/Tidal-Media-Downloader)
- [scdl](https://github.com/flyingrub/scdl)



## Disclaimer


I will not be responsible for how you use `streamrip`. By using `streamrip`, you agree to the terms and conditions of the Qobuz API.