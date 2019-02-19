# unifi-nvr-api-motion-mqtt-gifs
A python script to create animated gifs from videos recorded by cameras attached to UniFI Video NVR.

Supports [undocumented UniFI Video NVR API](https://dl.ubnt.com/guides/unifivideo/UniFi_Video_UG.pdf).

Supports multiple cameras polling and ffmpeg parameters

## Config File

Needs a simple JSON based config file passed in on the command line.

For example:

```json
{
  "mqtt_server": "broker.shiftr.io",
  "mqtt_port": 1883,
  "mqtt_user": "user",
  "mqtt_pwd": "password",
  "mqtt_base_topic": "unifi/cameras/gifs",
  "ffmpeg_working_folder": "./gifs",
  "unifi_video_base_api_url": "http://127.0.0.1",
  "unifi_video_user": "admin",
  "unifi_video_password": "password123",
  "unifi_cameras": [
    {
      "_id": "5c561db5ded6a576d76356fd",
      "skip_first_n_secs": 5, //<-- Skip seconds recorded before motion event is triggered
      "max_length_secs": 5, //<-- Do not create gif for video full length but only with first n seconds
      "scale": 320, //<-- Determine quality and size of the output gif
      "topic_name": "camera_1" //<-- Configurable camera topic name
    },
    {
      "_id": 2,
      "skip_first_n_secs": 7,
      "max_length_secs": 10,
      "scale": 640,
      "topic_name": "camera_2"
    }
  ]
}

```
* `mqtt_server`: MQTT server to publish notifications to
* `mqtt_port`: Port of MQTT server
* `mqtt_user`: Username of MQTT server
* `mqtt_pwd`: Password of MQTT server
* `mqtt_base_topic`: MQTT topic to publish new GIFs to.
* `ffmpeg_working_folder`: Working folder for downloaded mp4 videos and created GIFs
* `unifi_video_base_api_url`: Base url of Unifi Video NVR APIs
* `unifi_video_user`: User to access Unifi Video NVR APIs
* `unifi_video_password`: User's password to access Unifi Video NVR APIs
* `unifi_cameras`: Array of cameras for events polling
    * `_id`: Unifi Video NVR camera id
    * `skip_first_n_secs`: Skip seconds recorded before motion event is triggered
    * `max_length_secs`: Do not create gif for video full length but only with first n seconds
    * `scale`: Determine quality and size of the output gif
    * `topic_name`: Configurable camera topic name that will be appended to the end of this base topic

If you don't know camera ids, leave cameras section empty and you'll get ids printed at first run
```
"unifi_cameras": []
```
Example:
```
[2019-02-19 14:52:21] [INFO] (MainThread) Unifi NVR Video Info Camera Id UVC_G3_1 Name 5c561db5ded6a576d76356fd Id 192.168.1.105 IP 192.168.1.105 Model UVC G3 State CONNECTED LastRecordingId 5c6c044bacfa90ae35ecec86
```

## Installation

There is a docker image if you prefer to run using docker. For example:

```shell
docker run -v $(pwd)/config:/config \
    -v $(pwd)/gifs:/gifs \
    fabtesta/unifi-nvr-api-motion-mqtt-gifs:latest
```

or via docker compose.

```yaml
services:
  unifi-nvr-api-motion-mqtt-gifs:
    image: fabtesta/unifi-nvr-api-motion-mqtt-gifss:latest
    volumes:
      - ./config:/config
      - ./gifs:/gifs
    restart: unless-stopped
```

If you'd prefer to install dependencies yourself, you'll need:

* ffmpeg 4.0 (other versions probably work, but that's what I tested with)
* Python 3.7
* python libraries listed in `requirements.txt` (install via `pip install -r requirements.txt`)