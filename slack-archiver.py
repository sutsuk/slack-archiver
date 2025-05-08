#! /usr/bin/python3

import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import mysql.connector
import sys
import re
import requests
import mimetypes
import pytz
import time as Time
import json
import base64
import magic
from datetime import datetime
from datetime import timedelta

SLACK_API_TOKEN  = "SLACK_API_TOKEN"
SLACK_BOT_TOKEN  = "SLACK_BOT_TOKEN"
SLACK_USERS      = "https://slack.com/api/users.list"
SLACK_CHANNELS   = "https://slack.com/api/conversations.list?types=public_channel,private_channel"
SLACK_HISTORY    = "https://slack.com/api/conversations.history"
SLACK_REPLIES    = "https://slack.com/api/conversations.replies"

class MySQLManager():
  mysql_connection = None
  mysql_cursor = None
  def __del__(self):
    self.disconnect()
  def connect(self):
    self.mysql_connection = mysql.connector.connect(
      user="webserver",
      password="password",
      host="localhost",
      database="SlackArchiver"
    )
    if not self.mysql_connection.is_connected():
      raise Exception("Connection Failed")
    else:
      print("Connected")
    self.mysql_cursor = self.mysql_connection.cursor(dictionary=True)
  def disconnect(self):
    if self.mysql_cursor is not None:
      self.mysql_cursor.close
      self.mysql_cursor = None
    if self.mysql_connection is not None:
      self.mysql_connection.close
      self.mysql_connection = None
  def select(self, query):
    self.mysql_cursor.execute(query)
    result = self.mysql_cursor.fetchall()
    return result
  def insert(self, query):
    self.mysql_cursor.execute(query)
    self.mysql_connection.commit()
  def update(self, query):
    self.mysql_cursor.execute(query)
    self.mysql_connection.commit()

man = MySQLManager()
man.connect()

def mysql_insert(user, channel, text, name, data, date, time):
  text = text.replace('\\', '\\\\').replace('\'', '\\\'')
  result = man.select(f"select * from ArchivedData where date='{date}' and time='{time}' and name='{name}' and text='{text}'")
  if len(result) == 0:
    print("Insert")
    man.insert(f"insert into ArchivedData(id,user,channel,text,name,data,date,time) values(0,'{user}','{channel}','{text}','{name}','{data}','{date}','{time}');")
    return True
  return False

def get_ts(enforce=False, ddays=7, dhours=0, dminutes=3):
  ts_format = "%Y-%m-%d %H:%M:%S.%f"
  with open("loaded_ts", mode="r") as f:
    for line in f:
      logged_ts = line.rstrip()
      logged_dt = datetime.strptime(logged_ts, ts_format)
  start_ts = logged_dt - timedelta(days=ddays)
  with open("loaded_ts", mode='w') as f:
    now_ts = datetime.now().strftime(ts_format)
    end_ts = datetime.strptime(now_ts, ts_format)
    if (not enforce) and end_ts - start_ts < timedelta(days=ddays, hours=dhours, minutes=dminutes):
      f.write(f"{logged_ts}\n")
      return [None, None]
    else:
      f.write(f"{logged_ts}\n{now_ts}\n")
  return [start_ts.strftime('%s'), end_ts.strftime('%s')]

def get_users():
  users = {}
  headers = {}
  headers['Authorization'] = f"Bearer {SLACK_API_TOKEN}"
  data = requests.get(SLACK_USERS, headers=headers).json()
  if 'members' in data:
    mems = data['members']
    for i in range(len(mems)):
      if 'id' not in mems[i]: continue
      users[mems[i]['id']] = mems[i]['real_name'] if 'real_name' in mems[i] else ""
      if 'name' in mems[i]:
        users[mems[i]['id']] = mems[i]['name']    if users[mems[i]['id']] == "" else users[mems[i]['id']]
      users[mems[i]['id']] = re.sub(r"[\u3000 \t]", "", users[mems[i]['id']])
      result = man.select(f"select * from SlackUsers where user_id='{mems[i]['id']}';")
      if len(result) == 0:
        man.insert(f"insert into SlackUsers(id,user_id,user_name) values(0,'{mems[i]['id']}','{users[mems[i]['id']]}')") 
  return users

def get_chs():
  headers = {}
  headers['Authorization'] = f"Bearer {SLACK_API_TOKEN}"
  data = requests.get(SLACK_CHANNELS, headers=headers).json()
  print(data)
  return data['channels'] if 'channels' in data else []

def get_replies(ch_id, ch_name, ts, users):
  headers = {}
  headers['Authorization'] = f"Bearer {SLACK_API_TOKEN}"
  params = {}
  params['channel'] = ch_id
  params['ts']      = ts
  ch_rep = requests.get(SLACK_REPLIES, headers=headers, params=params).json()
  if 'messages' in ch_rep:
    msgs     = ch_rep['messages']
    msgs_len = len(msgs)
    for k in range(msgs_len):
      if 'ts' in msgs[k]:
        ts   = float(msgs[k]['ts']);
        dt   = datetime.fromtimestamp(ts, tz=pytz.timezone("Asia/Tokyo"))
        date = dt.strftime('%Y-%m-%d')
        time = dt.strftime('%H:%M:%S')
        timestamp = dt.strftime("%Y%m%d%H%M%S")
      else:
        date = "0000-00-00"
        time = "00:00:00"
        timestamp = "00000000000000"
      if 'user' in msgs[k]:
        if msgs[k]['user'] in users:
          user = users[msgs[k]['user']]
        else:
          raise Exception("Users data are empty")
      else:
        user = "Unknown"
      if 'text' in msgs[k]:
        text = msgs[k]['text']
        forward_text(user, ch_name, text, date, time)
      if 'files' in msgs[k]:
        files = msgs[k]['files']
        for l in range(len(files)):
          name = files[l]['name'] if 'name' in files[l] else 'unknown.file'
          if 'url_private_download' in files[l]:
            URL  = files[l]['url_private_download']
            forward_file(user, ch_name, name, URL, date, time, timestamp)
  return 0

def get_hist(ch_id, ch_name, start_ts, end_ts, users):
  headers = {}
  headers['Authorization'] = f"Bearer {SLACK_API_TOKEN}"
  params = {}
  params['channel'] = ch_id
  params['count']   = "1000000"
  params['oldest']  = start_ts
  params['latest']  = end_ts
  ch_hist = requests.get(SLACK_HISTORY, headers=headers, params=params).json()
  if 'messages' in ch_hist:
    msgs     = ch_hist['messages']
    msgs_len = len(msgs)
    for i in range(msgs_len):
      k = msgs_len - i - 1
      if 'ts' in msgs[k]:
        get_replies(ch_id, ch_name, msgs[k]['ts'], users)
      else:
        print("ts is not found.")
  return 0

def forward_text(user, ch, text, date, time):
  mysql_insert(user, ch, text, '', '', date, time)
  print(f"{date} {time} {user}@{ch} {text}")

def forward_file(user, ch, name, URL, date, time, timestamp):
  headers = {"Authorization": f"Bearer {SLACK_API_TOKEN}"}
  raw     = requests.get(URL, headers=headers, allow_redirects=True, stream=True).content
  full_parent_path = f"/home/nfs1/Nginx/slack-archiver/ArchivedData/{timestamp}/"
  parent_path = f"ArchivedData/{timestamp}/"
  file_ext = name.split('.')[-1]
  file_name = f"{name}.txt" if "php" in file_ext else f"{name}"
  status = mysql_insert(user, ch, '', name, f"{parent_path}{file_name}", date, time)
  if status:
    if not os.path.exists(full_parent_path):
      os.mkdir(full_parent_path)
    with open(f"{full_parent_path}{file_name}", 'wb') as f:
      f.write(raw)
  print(f"{date} {time} {user}@{ch} {full_parent_path} {parent_path} {file_name} ")

def main():
  if len(sys.argv) > 1 and sys.argv[1] == "ENFORCE":
    start_ts, end_ts = get_ts(enforce=True)
  else:
    start_ts, end_ts = get_ts()
  users = get_users()
  if start_ts is None:
    return 0
  for ch in get_chs():
    get_hist(ch['id'], ch['name'], start_ts, end_ts, users)

if __name__ == '__main__': main()
