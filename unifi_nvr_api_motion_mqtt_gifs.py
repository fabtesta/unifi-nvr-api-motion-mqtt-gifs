#!/usr/bin/env python3
import datetime
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from sqlite3 import Error

import paho.mqtt.client as mqtt
import requests
from requests import HTTPError

logging.basicConfig(level=logging.DEBUG,
                    format='[%(asctime)s] [%(levelname)s] (%(threadName)-10s) %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

# SYNO.API urls
unifiApiLoginUrl = "{}/api/2.0/login"
unifiApiServerInfoUrl = "{}/api/2.0/server"
unifiApiCamerasInfoUrl = "{}/api/2.0/camera"
unifiApiCameraInfoUrl = "{}/api/2.0/camera/{}"
unifiApiRecordingInfoUrl = "{}/api/2.0/recording/{}"
unifiApiRecordingDownloadUrl = "{}/api/2.0/recording/{}/download"

sql_create_processed_events_table = """ CREATE TABLE IF NOT EXISTS processed_events (
                                        id integer PRIMARY KEY,
                                        camera_id text NOT NULL,
                                        last_event_id text NOT NULL,
                                        last_recording_start_time int NOT NULL,
                                        processed_date timestamp NOT NULL
                                    ); """

sql_create_processed_events_table_unique = """ CREATE UNIQUE INDEX IF NOT EXISTS idx_processed_events_camera ON processed_events (camera_id); """


def parse_config(config_path):
    with open(config_path, 'r') as config_file:
        config_data = json.load(config_file)
    return config_data


def create_connection(data_folder):
    try:
        conn = sqlite3.connect(data_folder + '/processed_events.db')
        print(sqlite3.version)
        return conn
    except Error as e:
        logging.error("CANNOT CREATE DB", e)

    return None


def create_processed_events_table(conn):
    try:
        c = conn.cursor()
        c.execute(sql_create_processed_events_table)
        c.execute(sql_create_processed_events_table_unique)
    except Error as e:
        logging.error("CANNOT CREATE TABLE", e)


def check_already_processed_event_by_camera(conn, camera_id, event_id, last_recording_start_time):
    cur = conn.cursor()
    cur.execute("SELECT * FROM processed_events WHERE camera_id=? AND last_recording_start_time>=?",
                (camera_id, last_recording_start_time))

    rows = cur.fetchall()

    already_processed = False
    for row in rows:
        logging.error("Event %s already processed %s", event_id, row)
        already_processed = True

    return already_processed


def replace_processed_events(conn, processed_event):
    sql = ''' REPLACE INTO processed_events(camera_id, last_event_id, last_recording_start_time, processed_date)
              VALUES(?,?,?,?) '''
    cur = conn.cursor()
    cur.execute(sql, processed_event)

    conn.commit()
    return cur.lastrowid


def unifi_login(base_url, user, password):
    session = requests.Session()
    login_response = session.post(unifiApiLoginUrl.format(base_url), json={"username": user, "password": password},
                                  verify=False)
    logging.info('login_response status_code %s', login_response.status_code)

    if login_response.ok:
        login_data = json.loads(login_response.content.decode('utf-8'))
        if login_data["data"]:
            logging.info('login_response got JSESSIONID_AV for username %s',
                         login_data["data"][0]["account"]["username"])
            return session
        else:
            return None

    else:
        login_response.raise_for_status()


def unifi_server_info(base_url, session):
    server_info_response = session.get(unifiApiServerInfoUrl.format(base_url), verify=False)
    logging.info('server_info_response status_code %s', server_info_response.status_code)

    if server_info_response.ok:
        info_data = json.loads(server_info_response.content.decode('utf-8'))
        return info_data

    else:
        server_info_response.raise_for_status()


def unifi_cameras_info(base_url, session):
    cameras_info_response = session.get(unifiApiCamerasInfoUrl.format(base_url), verify=False)
    logging.info('cameras_info_response status_code %s', cameras_info_response.status_code)

    if cameras_info_response.ok:
        info_data = json.loads(cameras_info_response.content.decode('utf-8'))
        return info_data

    else:
        cameras_info_response.raise_for_status()


def unifi_camera_info(base_url, camera_id, session):
    camera_info_response = session.get(unifiApiCameraInfoUrl.format(base_url, camera_id), verify=False)
    logging.info('camera_info_response status_code %s', camera_info_response.status_code)

    if camera_info_response.ok:
        info_data = json.loads(camera_info_response.content.decode('utf-8'))
        return info_data["data"][0]

    else:
        camera_info_response.raise_for_status()


def camera_recording_info(base_url, recording_id, session):
    recording_response = session.get(unifiApiRecordingInfoUrl.format(base_url, recording_id),
                                     verify=False)
    logging.info('recordings_response status_code %s', recording_response.status_code)

    if recording_response.ok:
        recordings_data = json.loads(recording_response.content.decode('utf-8'))
        if len(recordings_data["data"]) > 0:
            logging.info('found recording info for id %s', recording_id)
            return recordings_data["data"][0]
        else:
            return None

    else:
        recording_response.raise_for_status()


def unifi_download_video(download_dir, base_url, recording_id, session):
    outfile_gif = '{}/{}.mp4'.format(download_dir, recording_id)

    with open(outfile_gif, "wb") as f:
        logging.info('Downloading video for event id %s to %s .....', recording_id, outfile_gif)
        download_response = session.get(unifiApiRecordingDownloadUrl.format(base_url, recording_id),
                                        verify=False, stream=True)
        logging.info('download_response status_code %s', download_response.status_code)

        if download_response.ok:
            total_length = download_response.headers.get('content-length')

            if total_length is None:  # no content length header
                f.write(download_response.content)
            else:
                dl = 0
                total_length = int(total_length)
                for data in download_response.iter_content(chunk_size=4096):
                    dl += len(data)
                    f.write(data)
                    done = int(50 * dl / total_length)
                    sys.stdout.write("\r[%s%s]" % ('=' * done, ' ' * (50 - done)))
                    sys.stdout.flush()
            logging.info('Downloading video for event id %s to %s .....DONE', recording_id, outfile_gif)
            return outfile_gif

        else:
            download_response.raise_for_status()


def convert_video_gif(scale, skip_first_n_secs, max_length_secs, input_video, output_gif):
    logging.info('convert_video_gif scale %i skip_first_n_secs %i max_length_secs %i input_video %s output_gif %s',
                 scale, skip_first_n_secs, max_length_secs, input_video, output_gif)

    retcode = subprocess.call([
        "ffmpeg", "-stats", "-i", input_video, "-vf",
        "fps=15,scale={}:-1:flags=lanczos".format(scale),
        "-ss", "00:00:" + "{}".format(skip_first_n_secs).zfill(2), "-t", "{}".format(max_length_secs), "-y",
        str(output_gif)
    ])
    os.remove(input_video)
    return retcode


class CameraMotionEventHandler:
    def __init__(self, processed_events_conn, base_url, camera, config, session):
        self.base_url = base_url
        self.camera = camera
        self.config = config
        self.session = session
        self.mqtt_client = mqtt.Client()
        self.mqtt_client.username_pw_set(username=self.config["mqtt_user"], password=self.config["mqtt_pwd"])
        # Keep a FIFO of files processed so we can guard against duplicate
        # events
        self.processed_events_conn = processed_events_conn

    def publish_event(self, event):
        event_file = Path(event.mp4_path)
        gif = self.convert_gif(event_file)
        if gif:
            self.publish_mqtt_message(gif, self.camera.topic_name)

    def publish_mqtt_message(self, gif):
        logging.info('publish_mqtt_message gif mqtt_server %s  mqtt_port %i mqtt_base_topic %s topic_name %s',
                     self.config["mqtt_server"], self.config["mqtt_port"], self.config["mqtt_base_topic"],
                     self.camera["topic_name"])

        self.mqtt_client.connect(self.config["mqtt_server"],
                                 self.config["mqtt_port"])
        retcode = self.mqtt_client.publish(
            self.config["mqtt_base_topic"] + "/" + self.camera["topic_name"], gif)
        return retcode

    def poll_recording(self):
        logging.info('Start getting last camera event for camera %s %s', self.camera["_id"], self.camera["topic_name"])
        camera_info = unifi_camera_info(self.base_url, self.camera["_id"], self.session)
        if camera_info:
            if check_already_processed_event_by_camera(self.processed_events_conn, self.camera["_id"],
                                                       camera_info["lastRecordingId"],
                                                       camera_info["lastRecordingStartTime"]):
                logging.info('Recording %s already processed', camera_info["lastRecordingId"])
                return None, None

            # check if recording is in progress
            recording_info = camera_recording_info(self.base_url, camera_info["lastRecordingId"], self.session)
            if recording_info:
                if recording_info["inProgress"] is True:
                    logging.info('Recording %s is in progress skip for now', camera_info["lastRecordingId"])
                    return None, None
            else:
                logging.info('No recording info found for recording_id %s for camera %s %s',
                             camera_info["lastRecordingId"]
                             , self.camera["_id"], self.camera["topic_name"])
                return None, None

            logging.info('Start downloading event video for recording_id %s', camera_info["lastRecordingId"])
            mp4_file = unifi_download_video(self.config["ffmpeg_working_folder"], self.base_url,
                                            camera_info["lastRecordingId"], self.session)
            outfile_gif = '{}/{}.gif'.format(self.config["ffmpeg_working_folder"],
                                             camera_info["lastRecordingId"])
            convert_retcode = convert_video_gif(self.camera["scale"],
                                                self.camera["skip_first_n_secs"],
                                                self.camera["max_length_secs"],
                                                mp4_file, outfile_gif)
            if convert_retcode == 0:
                public_retcode = self.publish_mqtt_message('{}.gif'.format(camera_info["lastRecordingId"]))
                if public_retcode:
                    processed_event = (
                        self.camera["_id"], camera_info["lastRecordingId"], camera_info["lastRecordingStartTime"],
                        datetime.datetime.now())
                    replace_processed_events(self.processed_events_conn, processed_event)
                    logging.info('Done processing recording_id %s', camera_info["lastRecordingId"])
                else:
                    logging.error('Invalid return code from mqtt publish for recording_id %s camera topic %s',
                                  camera_info["lastRecordingId"],
                                  self.camera["topic_name"])
            else:
                logging.error('Invalid return code from ffmpeg subprocess call for recording_id %s',
                              camera_info["lastRecordingId"])
        else:
            logging.info('No recording found for camera %s %s', self.camera["_id"], self.camera["topic_name"])


def main():
    _, config_filename = sys.argv
    logging.info('Starting')
    logging.info('Parsing %s', config_filename)
    config = parse_config(config_filename)

    config_data_folder = ''
    if 'data_folder' in config:
        config_data_folder = config["data_folder"]
    if config_data_folder == '':
        config_data_folder = "/data"

    logging.info('Creating/Opening processed_events database on file %s', config_data_folder)
    processed_events_conn = create_connection(config_data_folder)
    if processed_events_conn is not None:
        # create processed_events table
        create_processed_events_table(processed_events_conn)
    else:
        logging.error('Error! cannot create the database connection.')
        return

    session = None
    logged_in = False
    running = True

    while running:
        try:
            if session is None or logged_in is False:
                session = unifi_login(config["unifi_video_base_api_url"], config["unifi_video_user"],
                                      config["unifi_video_password"])
                if session is None:
                    logging.error('Unifi NVR Video credentials not valid')
                    continue
                else:
                    logged_in = True
                    logging.info('Unifi NVR Video Auth ok JSESSIONID_AV %s', session.headers.get('JSESSIONID_AV'))
                    server_data = unifi_server_info(config["unifi_video_base_api_url"], session)
                    for server_info in server_data["data"]:
                        logging.info('Unifi NVR Video Info Server Name %s Version %s IP %s', server_info["name"],
                                     server_info["firmwareVersion"], server_info["host"])

                    cameras_data = unifi_cameras_info(config["unifi_video_base_api_url"], session)
                    for camera_info in cameras_data["data"]:
                        logging.info('Unifi NVR Video Info Camera Id %s Name %s Id %s IP %s Model '
                                     '%s State %s LastRecordingId %s',
                                     camera_info["name"], camera_info["_id"], camera_info["host"],
                                     camera_info["host"], camera_info["model"], camera_info["state"],
                                     camera_info["lastRecordingId"])

            for camera in config["unifi_cameras"]:
                logging.info('CameraMotionEventHandler poll_recording %s %s', camera["_id"], camera["topic_name"])
                camera_handler = CameraMotionEventHandler(processed_events_conn, config["unifi_video_base_api_url"],
                                                          camera,
                                                          config, session)
                camera_handler.poll_recording()

        except KeyboardInterrupt:
            logging.info('KeyboardInterrupt')
            running = False
        except HTTPError:
            logging.info('HTTPError.retrying...')
        finally:
            if running:
                time.sleep(10)

    logging.info('Ending')


if __name__ == "__main__":
    main()
