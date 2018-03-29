# -*- coding: utf-8 -*-

import threading, signal
import traceback
import sys
import time
import serial
import sqlite3
import platform
import subprocess
import os
import socket
import urllib
import urllib2
import re
import json
import logging
import glob
import ssl
    
ssl._create_default_https_context = ssl._create_unverified_context
ID = ""
# HOST = "connect.iceman3d.net"
HOST = "connect.wanhao3d.com"
GcodeFile = ""
gcodePos = 0
_serial = None
_socket = None
isDiskMode = True
isStopPrint = False
isGet = True
gcodeList = []
gcodeListSize = 0
file_size = 0

TarTemp = 200
CurTemp = 0
PrintMode = 0
CurrentZ = 0
PrintReadSize = 0
PrintTime = 0
#serialList = []
serialList = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*') + ['/dev/ttyS1']
baudrate = 115200

#LED
WifiLed = 0
BindLed = 0
PrintLed = 0
isSmartWifiMode = False

def quit(signum, frame):
    print('stop dayin.la')
    _serial.close()
    _socket.close()
    os._exit(1)
    sys.exit(1)

def kill(pid):
    try:
        a = os.kill(pid, signal.SIGKILL)
        print('kill pid %s,code:%s' % (pid, a))
        global WifiLed, BindLed, PrintLed
        WifiLed = 0
        BindLed = 0
        PrintLed = 0
    except:
        traceback.print_exc()
        
def getID():
    global ID
    if ID == "":
        output = os.popen("cat /sys/class/net/ra0/address")
        macStr = output.read()
        print(macStr)
        #macStr = "0c:ef:af:cf:e1:b2"
        macStr = macStr.upper()
        ID = ("WHNE"+"".join(macStr.split(':')))[0:16]
        #checksum = reduce(lambda x,y:x^y, map(ord, "DYL%s" % ID))
    print("ID=>", ID)
    return ID
    
def getDiskStats(whichdir):
    total = 0
    free_for_root = 0
    free_for_nonroot = 0
    try:
        s = os.statvfs(whichdir)
        total = s.f_frsize * s.f_blocks
        free_for_root = s.f_frsize * s.f_bfree
        free_for_nonroot = s.f_frsize * s.f_bavail
    except:
        pass
    return {'total': total, 'free_for_root': free_for_root, 'free_for_nonroot': free_for_nonroot}

def setDiskMode():
    global isDiskMode
    diskStats = getDiskStats('/tmp/mounts/SD-P1')
    isDiskMode = (diskStats["free_for_root"]>(10*1024*1024*1024))
    print("isDiskMode=>", isDiskMode)
    return isDiskMode

def checksumGcode(n, gcode):
    checksum = reduce(lambda x,y:x^y, map(ord, "N%d%s" % (n, gcode)))
    return ("N%d%s*%d" % (n, gcode, checksum))
    
def serialSend(msg):
    global _serial
    try:
        print("serial=>", msg)
        _serial.write(msg+'\n')
    except:
        traceback.print_exc()
        _serial.close()
        serialList = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*') + ['/dev/ttyS1']
        if len(serialList)>0 :
            _serial = serial.Serial(serialList[0], 115200, timeout=1)
        
def socketSend(msg):
    global _socket, WifiLed
    try:
        print("socket=>", msg)
        _socket.send(msg+'\n')
        #WifiLed = 0
    except:
        traceback.print_exc()
        WifiLed = 0
        
def sendHeartBeat():
    global TarTemp, CurTemp, PrintMode, CurrentZ, PrintReadSize, PrintTime
    beat = "BEAT:%d|%d|%d|%d|%d|%d|%d|%d|%d|%d|%d\n" % (TarTemp, CurTemp, PrintMode, 100, CurrentZ, 100, PrintReadSize, 0 if PrintTime==0 else int(time.time() - PrintTime), 0, 100, 100)
    # print(beat)
    socketSend(beat)
    
def download(info):
    global isGet, GcodeFile, isDiskMode, gcodeList, gcodeListSize, file_size, WifiLed
    if isDiskMode:
        #global isGet
        isGet = True
        def callbackfunc(block_read, block_size, total_size):
            global isGet, _socket, file_size
            if isGet:
                isGet = False
                file_size = total_size
                socketSend("3DToAPP:wifiPrintStart:%d" % total_size)
            per = 100.0 * block_read * block_size / total_size
            print("3DToAPP:downloading:%d\n" % per)
            socketSend("3DToAPP:downloading:%d" % (block_read * block_size))  
        try:
            urllib.urlretrieve(info[0], ("/tmp/mounts/SD-P1/%s.g" % urllib.quote(info[1]).decode('utf8')), callbackfunc)
            print("downloaded")
            _socket.send("3DToAPP:downloaded:\n")
            #global GcodeFile
            #GcodeFile = info[1].decode('utf8')
            GcodeFile = info[1]            
            serialSend("M105")
            WifiLed = 3
            # PrintLed = 3
        except:
            traceback.print_exc()
            socketSend("3DToAPP:downloaderror:")
            serialSend("M117 Download Error")
    else:
        try:
            gcodeList = []
            gcodeListSize = 0
            isGet = True
            WifiLed = 1
            params = {"filename": info[1].decode('utf8'), "printMode": 0, "printStartTime":int(time.time()), "printTotalSize":0, "printTotalTime":0, "materialLength":0 }
            ul = urllib2.urlopen(info[0])
            meta = ul.info()
            file_size = int(meta.getheaders("Content-Length")[0])
            print(file_size)
            params["printTotalSize"] = file_size
            socketSend("3DToAPP:wifiPrintStart:%d" % file_size)
            file_size_dl = 0
            block_sz = 8192
            redundant = ""
            while True:
                buffer = ul.read(block_sz)
                if not buffer:
                    break
                file_size_dl += len(buffer)
                # print("=>",file_size_dl)
                if file_size_dl%(block_sz*16)==0:
                    print("3DToAPP:downloading:%d" % file_size_dl)
                    socketSend("3DToAPP:downloading:%d" % file_size_dl) 
                data = redundant+str(buffer)
                cmds = data.split('\n')
                if cmds[-1].strip() == '':
                    redundant = ""
                else:
                    redundant = cmds[-1]
                cmds[-1] = ""
                for line in cmds:
                    line = line.strip()
                    if line != '':
                        #print(line)
                        #gcodeList.append(line)
                        commentPos = line.find(';')
                        if commentPos == -1:
                            #line = line[0:line.find(';')].strip()
                            if len(line) > 0:
                                #print(len(gcodeList), line)
                                gcodeList.append(line)
                                if isGet:
                                    if line.startswith("G0") or line.startswith("G1"):
                                        isGet = False
                                        print("socket=>SliceParams" + json.dumps(params))
                                        socketSend("SliceParams:" + json.dumps(params))
                        else:
                            if line.startswith(";id:"):
                                print(line[4:].strip())
                                params["id"] = line[4:].strip()
                            elif line.startswith(";images:"):
                                print(line[8:].strip())
                                params["imageUrl"] = line[8:].strip()
                            elif line.startswith(";Layer count:"):
                                print(line[13:].strip())
                                params["zMax"] = line[13:].strip()
                            elif line.startswith(";settings:"):
                                try:
                                    param = re.search(";settings:\\s*(\\w+):\\s*(.*)", line)
                                    print(param.group(1), "=>", param.group(2))
                                    key = param.group(1).strip()
                                    value = param.group(2).strip()
                                    if key == "suppor_surface":
                                        params["supporSurface"] = value
                                    elif key == "layer_height":
                                        params["layerHeight"] = value
                                    elif key == "support":
                                        params["support"] = value
                                    elif key == "platform_adhesion":
                                        params["platformAdhesion"] = value
                                    elif key == "bottom_layer_speed":
                                        params["bottomLayerSpeed"] = value
                                    elif key == "print_speed":
                                        params["printSpeed"] = value
                                    elif key == "retraction_amount":
                                        params["retractionAmount"] = value
                                    elif key == "retraction_speed":
                                        params["retractionSpeed"] = value
                                    elif key == "retraction_enable":
                                        params["retractionEnable"] = value
                                    elif key == "wall_count":
                                        params["wallCount"] = value
                                    elif key == "solid_layer_count":
                                        params["solidLayerCount"] = value
                                    elif key == "support_z_distance":
                                        params["supportZDistance"] = value
                                    elif key == "support_xy_distance":
                                        params["supportXyDistance"] = value
                                    elif key == "support_fill_rate":
                                        params["supportFillRate"] = value
                                    elif key == "support_angle":
                                        params["supportAngle"] = value
                                    elif key == "travel_speed":
                                        params["travelSpeed"] = value
                                    elif key == "fill_density":
                                        params["fillDensity"] = value
                                    elif key == "filament_diameter":
                                        params["filamentDiameter"] = value
                                except:
                                    pass
                            elif line.startswith(";Sliced at:"):
                                #print(line[11:].strip())
                                #params["sliceTime"] = line[11:].strip()
                                param = re.search("(\\d{2}-\\d{2}-\\d{4} \\d{2}:\\d{2}:\\d{2})", line)
                                print(param.group(1))
                                params["sliceTime"] = str(int(time.mktime(time.strptime(param.group(1),'%d-%m-%Y %H:%M:%S'))))
                            elif line.startswith(";Print time:"):
                                print(line[12:].strip())
                                params["printTotalTime"] = line[12:].strip()
                            elif line.startswith(";Filament used:"):
                                try:
                                    param = re.search(";Filament used:\\s*(.*) \\s*(.*)", line)
                                    print(param.group(1), "=>", param.group(2))
                                    params["materialLength"] = param.group(1).strip()
                                    params["printTotalSize"] = param.group(2).strip()
                                except:
                                    pass
            socketSend("3DToAPP:downloaded:")
            # serialSend("M117 Downloaded!!!")
            #global GcodeFile
            GcodeFile = "memory"
            gcodeListSize = len(gcodeList)
            serialSend("M105")
            WifiLed = 3
            # PrintLed = 3
        except:
            traceback.print_exc()
            socketSend("3DToAPP:downloaderror:")
            serialSend("M117 Download Error")
            PrintLed = 2
        

#定时检查串口
def CheckTimer():
    global serialList
    serialList = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*') + ['/dev/ttyS1']
    print("serialList",serialList)
    checkTimer = threading.Timer(30, CheckTimer)
    checkTimer.start()

#控制LED
class LedThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.startTime = 0
        self.offLastLed = False
        os.system("echo 15 > /sys/class/gpio/export")
        os.system("echo out > /sys/class/gpio/gpio15/direction")
        os.system("echo 16 > /sys/class/gpio/export")
        os.system("echo out > /sys/class/gpio/gpio16/direction")
        os.system("echo 17 > /sys/class/gpio/export")
        os.system("echo out > /sys/class/gpio/gpio17/direction")
        
    def run(self):
        print("LedThread start")
        global WifiLed, BindLed, PrintLed
        SlowTimer = 0
        WifiLedGpioValue = 0
        BindLedGpioValue = 0
        PrintLedGpioValue = 0
        SyncLedValue = 0
        SyncSlowLedValue = 0
        while True:
            if SyncLedValue == 0:
                SyncLedValue = 1
            else:
                SyncLedValue = 0
            
            if SlowTimer == 0:
                if SyncSlowLedValue == 0:
                    SyncSlowLedValue = 1
                else:
                    SyncSlowLedValue = 0
            
            if WifiLed == 0:
                if WifiLedGpioValue == 1:
                    WifiLedGpioValue = 0
                os.system("echo %d > /sys/class/gpio/gpio15/value" % (WifiLedGpioValue))
            elif WifiLed == 1:                
                WifiLedGpioValue = SyncLedValue
                os.system("echo %d > /sys/class/gpio/gpio15/value" % (WifiLedGpioValue))
            elif WifiLed == 2:
                if SlowTimer == 0:
                    WifiLedGpioValue = SyncSlowLedValue
                    os.system("echo %d > /sys/class/gpio/gpio15/value" % (WifiLedGpioValue))
            else:
                if WifiLedGpioValue == 0:
                    WifiLedGpioValue = 1
                os.system("echo %d > /sys/class/gpio/gpio15/value" % (WifiLedGpioValue))

            if BindLed == 0:
                if BindLedGpioValue == 1:
                    BindLedGpioValue = 0
                os.system("echo %d > /sys/class/gpio/gpio16/value" % (BindLedGpioValue))
            elif BindLed == 1:
                BindLedGpioValue = SyncLedValue    
                os.system("echo %d > /sys/class/gpio/gpio16/value" % (BindLedGpioValue))
            elif BindLed == 2:
                if SlowTimer == 0:
                    BindLedGpioValue = SyncSlowLedValue    
                    os.system("echo %d > /sys/class/gpio/gpio16/value" % (BindLedGpioValue))
            else:
                if BindLedGpioValue == 0:
                    BindLedGpioValue = 1
                os.system("echo %d > /sys/class/gpio/gpio16/value" % (BindLedGpioValue))

            if PrintLed == 0:
                if PrintLedGpioValue == 1:
                    PrintLedGpioValue = 0
                os.system("echo %d > /sys/class/gpio/gpio17/value" % (PrintLedGpioValue))
            elif PrintLed == 1:
                PrintLedGpioValue = SyncLedValue    
                os.system("echo %d > /sys/class/gpio/gpio17/value" % (PrintLedGpioValue))
            elif PrintLed == 2:
                if SlowTimer == 0:
                    PrintLedGpioValue = SyncSlowLedValue    
                    os.system("echo %d > /sys/class/gpio/gpio17/value" % (PrintLedGpioValue))
            else:
                if PrintLedGpioValue == 0:
                    PrintLedGpioValue = 1
                os.system("echo %d > /sys/class/gpio/gpio17/value" % (PrintLedGpioValue))

            SlowTimer = (SlowTimer + 1)%10
            time.sleep(0.1)

#监听按钮，启动airkiss
class ButtonThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.gpio = 18
        self.isCLick = 0
        self.lastCLickTime = time.time()
        self.airkissing = False
        self.isOneClick = False
        #left button
        os.system("echo 18 > /sys/class/gpio/export")
        os.system("echo in > /sys/class/gpio/gpio18/direction")
        #right button
        os.system("echo 5 > /sys/class/gpio/export")
        os.system("echo in > /sys/class/gpio/gpio5/direction")

    def run(self):
        print("ButtonThread start")
        global WifiLed, BindLed, PrintLed, PrintMode
        buttonLeft = 0
        buttonRight = 0
        buttonLeftValue = open("/sys/class/gpio/gpio18/value", 'r+')
        buttonRightValue = open("/sys/class/gpio/gpio5/value", 'r+')
        while True:
            buttonLeftValue.seek(0)
            buttonLeftVal = buttonLeftValue.read()
            buttonRightValue.seek(0)
            buttonRightVal = buttonRightValue.read()

            #buttonLeft
            if int(buttonLeftVal) == 0:
                if buttonLeft == 0:
                    buttonLeft = time.time()
                else:
                    if time.time() - buttonLeft >= 6:  
                        WifiLed = 0  
                    elif time.time() - buttonLeft >= 3:
                        WifiLed = 1
                # print("buttonLeft", buttonLeft)
            else:
                if buttonLeft != 0:
                    if time.time() - buttonLeft < 1:
                        print("<1")
                        global PrintMode
                        if PrintMode == 0:
                            print("===>unbind")                
                            try:
                                global _socket
                                _socket.send("3DBindEsc:\n")
                                serialSend("M117 Bind Esc")
                                BindLed = 1
                            except:
                                traceback.print_exc()
                    elif time.time() - buttonLeft >= 6:  
                        print(">=6")  
                    elif time.time() - buttonLeft >= 3:
                        print(">=3")
                        if self.airkissing == False and PrintMode == 0:
                            self.airkissing = True
                            print("airkiss")
                            serialSend("M117 Start Smart WiFi")
                            WifiLed = 1
                            BindLed = 0
                            PrintLed = 0
                            #os.system("airkiss")
                            kwargs = {}
                            process = subprocess.Popen("airkiss", stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
                            print("airkiss pid", process.pid)
                            t = threading.Timer(90.0, kill, [process.pid])
                            t.start()
                            line = process.stdout.readline().strip()
                            while len(line) > 0:
                                #print("=>",line)
                                if line.find("AirKiss complete: ssid ")==0:
                                    pos1 = line.find('"',0)
                                    pos2 = line.find('"',pos1+1)
                                    pos3 = line.find('"',pos2+1)
                                    pos4 = line.find('"',pos3+1)
                                    ssid = line[pos1+1:pos2]
                                    pw = line[pos3+1:pos4]
                                    print(line, "ssid=>", ssid, "pw=>", pw)
                                    serialSend("M117 SSID:"+ssid)
                                    os.system("widora_mode client %s %s" % (ssid, pw))
                                    kill(process.pid)
                                line = process.stdout.readline().strip()
                            self.airkissing = False
                            print("airkiss end")
                            WifiLed = 0
                    buttonLeft = 0

            #buttonRight
            if int(buttonRightVal) == 0:
                if buttonRight == 0:
                    buttonRight = time.time()
                else:
                    if time.time() - buttonRight >= 6:  
                        PrintLed = 0  
                    elif time.time() - buttonRight >= 3:
                        PrintLed = 1
                # print("buttonRight", buttonRight)
            else:
                if buttonRight != 0:
                    if time.time() - buttonRight < 1:
                        print("<1")
                    elif time.time() - buttonRight >= 6:  
                        print(">=6")  
                    elif time.time() - buttonRight >= 3:
                        print(">=3")
                        global isStopPrint, TarTemp, CurTemp, CurrentZ, PrintReadSize, PrintTime, gcodePos, GcodeFile
                        # self.isOneClick = False
                        if PrintMode > 0:
                            print("=>stop print")   
                            serialSend("M117 Stop Print...")
                            serialSend("M104 S0")
                            serialSend("G28 X0 Y0")                         
                            isStopPrint = True
                            TarTemp = 0
                            CurTemp = 0
                            PrintMode = 0
                            CurrentZ = 0
                            PrintReadSize = 0
                            PrintTime = 0
                            PrintLed = 2
                            _socket.send("StopPrint:\n")
                        else:
                            if gcodeListSize > 0:
                                print("=>print last model")
                                setDiskMode()
                                isStopPrint = False
                                PrintMode = 3
                                gcodePos = 0
                                PrintTime = int(time.time())
                                sendHeartBeat()
                                time.sleep(1)
                                serialSend(checksumGcode(0, "M110"))
                                GcodeFile = ""
                                PrintLed = 3
                            else:
                                print("open last model")
                    buttonRight = 0
            time.sleep(0.1)
            
        
#连接服务器socket
class SocketThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        print("SocketThread init")
        global _socket,HOST
        _socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.heartBeatTimer = threading.Timer(5, self.HeartBeat)
        self.heartBeatTimer.start()
        try:            
            _socket.connect((HOST,28866))
            # _socket.settimeout(15)            
        except:
            traceback.print_exc()
            print("Connect server failed =>",HOST)
            _socket.close()
        
    def crc16(self,str):
        string = "ICEMAN3DPRINTER" + str
        auchCRCHi = [ 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, \
            0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, \
            0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, \
            0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, \
            0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, \
            0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, \
            0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, \
            0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, \
            0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, \
            0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, \
            0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, \
            0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, \
            0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, \
            0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, \
            0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, \
            0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, \
            0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, \
            0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, \
            0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, \
            0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, \
            0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, \
            0x80, 0x41, 0x00, 0xC1, 0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, \
            0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, 0x80, 0x41, 0x00, 0xC1, \
            0x81, 0x40, 0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, \
            0x00, 0xC1, 0x81, 0x40, 0x01, 0xC0, 0x80, 0x41, 0x01, 0xC0, \
            0x80, 0x41, 0x00, 0xC1, 0x81, 0x40]  
  
        auchCRCLo = [ 0x00, 0xC0, 0xC1, 0x01, 0xC3, 0x03, 0x02, 0xC2, 0xC6, 0x06, \
            0x07, 0xC7, 0x05, 0xC5, 0xC4, 0x04, 0xCC, 0x0C, 0x0D, 0xCD, \
            0x0F, 0xCF, 0xCE, 0x0E, 0x0A, 0xCA, 0xCB, 0x0B, 0xC9, 0x09, \
            0x08, 0xC8, 0xD8, 0x18, 0x19, 0xD9, 0x1B, 0xDB, 0xDA, 0x1A, \
            0x1E, 0xDE, 0xDF, 0x1F, 0xDD, 0x1D, 0x1C, 0xDC, 0x14, 0xD4, \
            0xD5, 0x15, 0xD7, 0x17, 0x16, 0xD6, 0xD2, 0x12, 0x13, 0xD3, \
            0x11, 0xD1, 0xD0, 0x10, 0xF0, 0x30, 0x31, 0xF1, 0x33, 0xF3, \
            0xF2, 0x32, 0x36, 0xF6, 0xF7, 0x37, 0xF5, 0x35, 0x34, 0xF4, \
            0x3C, 0xFC, 0xFD, 0x3D, 0xFF, 0x3F, 0x3E, 0xFE, 0xFA, 0x3A, \
            0x3B, 0xFB, 0x39, 0xF9, 0xF8, 0x38, 0x28, 0xE8, 0xE9, 0x29, \
            0xEB, 0x2B, 0x2A, 0xEA, 0xEE, 0x2E, 0x2F, 0xEF, 0x2D, 0xED, \
            0xEC, 0x2C, 0xE4, 0x24, 0x25, 0xE5, 0x27, 0xE7, 0xE6, 0x26, \
            0x22, 0xE2, 0xE3, 0x23, 0xE1, 0x21, 0x20, 0xE0, 0xA0, 0x60, \
            0x61, 0xA1, 0x63, 0xA3, 0xA2, 0x62, 0x66, 0xA6, 0xA7, 0x67, \
            0xA5, 0x65, 0x64, 0xA4, 0x6C, 0xAC, 0xAD, 0x6D, 0xAF, 0x6F, \
            0x6E, 0xAE, 0xAA, 0x6A, 0x6B, 0xAB, 0x69, 0xA9, 0xA8, 0x68, \
            0x78, 0xB8, 0xB9, 0x79, 0xBB, 0x7B, 0x7A, 0xBA, 0xBE, 0x7E, \
            0x7F, 0xBF, 0x7D, 0xBD, 0xBC, 0x7C, 0xB4, 0x74, 0x75, 0xB5, \
            0x77, 0xB7, 0xB6, 0x76, 0x72, 0xB2, 0xB3, 0x73, 0xB1, 0x71, \
            0x70, 0xB0, 0x50, 0x90, 0x91, 0x51, 0x93, 0x53, 0x52, 0x92, \
            0x96, 0x56, 0x57, 0x97, 0x55, 0x95, 0x94, 0x54, 0x9C, 0x5C, \
            0x5D, 0x9D, 0x5F, 0x9F, 0x9E, 0x5E, 0x5A, 0x9A, 0x9B, 0x5B, \
            0x99, 0x59, 0x58, 0x98, 0x88, 0x48, 0x49, 0x89, 0x4B, 0x8B, \
            0x8A, 0x4A, 0x4E, 0x8E, 0x8F, 0x4F, 0x8D, 0x4D, 0x4C, 0x8C, \
            0x44, 0x84, 0x85, 0x45, 0x87, 0x47, 0x46, 0x86, 0x82, 0x42, \
            0x43, 0x83, 0x41, 0x81, 0x80, 0x40] 
        
        crchi = 0xff  
        crclo = 0xff  
        for i in range(0,len(string)):  
            crcIndex = crclo ^ ord(string[i])  
            crclo = crchi ^ auchCRCHi[crcIndex]  
            crchi = auchCRCLo[crcIndex]  
        return '%x' % (crchi<<8 | crclo)
    
    def HeartBeat(self):
        global _socket
        if PrintMode == 0:
            serialSend("M105")
        else:
            sendHeartBeat()
        self.heartBeatTimer = threading.Timer(5, self.HeartBeat)
        self.heartBeatTimer.start()        

    def run(self):
        print("SocketThread start")      
        global _socket, WifiLed, BindLed, PrintLed, HOST, PrintMode
        try:
            redundant = ""
            while True:
                data = ""
                try:
                    data = redundant+_socket.recv(1024)
                except:
                    traceback.print_exc()
                    time.sleep(5)
                    try:
                        print("try connect " + HOST)
                        _socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        _socket.connect((HOST,28866))   
                        # _socket.settimeout(15)  
                    except:
                        traceback.print_exc()
                        _socket.close()
                        print("Connect server failed =>", HOST)
                cmds = data.split('\n')
                if cmds[-1] == '':
                    redundant = ""
                else:
                    redundant = cmds[-1]
                cmds[-1] = ''
                for cmd in cmds:
                    if cmd != '':                        
                        if cmd == "login":
                            _socket.send("3DLogin:%s|%s\n" % (getID(), "20161219"))
                            WifiLed = 2
                        else:
                            if False:
                                print("!checksum")
                            else:
                                cmd = cmd[1:]
                                print("socket<=",cmd)
                                if cmd.startswith("3DLogin:"):
                                    print("3DVerify:%s" % self.crc16(cmd))
                                    _socket.send("3DVerify:%s\n" % self.crc16(cmd))
                                elif cmd.startswith("3DVerify:"):
                                    # if cmd[9] == '0':
                                    _socket.send("3DCheckBind:\n")
                                    WifiLed = 3
                                    serialSend("M117 SN:"+getID())
                                elif cmd.startswith("3DCheckBind:"):
                                    if cmd[12] == '2':
                                        print("isBinding = 2")
                                        BindLed = 3
                                        if PrintMode == 0:
                                            serialSend("M117 Bind Succeed")
                                    elif cmd[12] == '1':
                                        print("isBinding = 1")
                                        BindLed = 3
                                        if PrintMode == 0:
                                            serialSend("M117 Bind Succeed")
                                    else:
                                        print("isBinding = 0")
                                        BindLed = 2
                                elif cmd.startswith("CheckPrint:"):
                                    if False:
                                        _socket.send("CheckPrint:1\n")
                                    else:
                                        _socket.send("CheckPrint:0\n")
                                elif cmd.startswith("APPTo3D:"):
                                    print(cmd[8:].strip())
                                    try:
                                        global _serial
                                        #print("_serial",_serial)
                                        _serial.write(str(cmd[8:].strip())+'\n')
                                    except:
                                        traceback.print_exc()
                                elif cmd.startswith("3DPrint:"):
                                    print(cmd[8:].strip())
                                elif cmd.startswith("StopPrint:"):
                                    print("StopPrint")
                                    serialSend("M117 Stop Print...")
                                    serialSend("M104 S0")
                                    serialSend("G28 X0 Y0")
                                    global isStopPrint, TarTemp, CurTemp, CurrentZ, PrintReadSize, PrintTime, gcodeList, gcodeListSize, PrintLed
                                    isStopPrint = True
                                    TarTemp = 0
                                    CurTemp = 0
                                    PrintMode = 0
                                    CurrentZ = 0
                                    PrintReadSize = 0
                                    PrintTime = 0
                                    #gcodeList = []
                                    #gcodeListSize = 0
                                    PrintLed = 2
                                    _socket.send("StopPrint:\n")
                                elif cmd.startswith("I:"):  
                                    PrintLed = 1                                  
                                    info = urllib.unquote(cmd[2:].strip()).split('|')
                                    print("url", info[0], "name", info[1].decode('utf8').encode('gbk'))
                                    serialSend("M117 Downloading...")
                                    serialSend("M104 S200")
                                    serialSend("M104 S200")
                                    setDiskMode()
                                    download(info)
                                elif cmd.startswith("CLOSE"):
                                    _socket.close()
                                    
        except:
            traceback.print_exc()
            time.sleep(3)
            print("_socket.close()")
            _socket.close()

#串口通讯打印
class SerialThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        print("SerialThread init")
        global _serial
        #_serial = serial.Serial("COM41" if platform.system()=="Windows" else "/dev/ttyS2", 230400, timeout=1)
        #_serial = serial.Serial("COM41" if platform.system()=="Windows" else "/dev/ttyACM0", 115200, timeout=1)
        print("serial", len(serialList), serialList[0], 115200 if len(serialList)>1 else 230400)
        _serial = serial.Serial(serialList[0], 115200 if len(serialList)>1 else 115200, timeout=1)
        try:
            sql = sqlite3.connect("/tmp/mounts/SD-P1/dayin.la.db")
            sql.execute('''PRAGMA journal_mode = OFF''')
            sql.execute("DROP TABLE IF EXISTS Gcode")
            sql.execute("CREATE TABLE IF NOT EXISTS Gcode(id INTEGER PRIMARY KEY, gcode text, pos int default 0)")
            sql.commit()
            sql.close()
        except:
            traceback.print_exc()
        
    def convertDb(self, sql, GcodeFile):
        print("gcode convert db")
        sql.execute("DELETE FROM Gcode")
        cur = sql.cursor()        
        lineNr=0
        #for line in open(GcodeFile, 'r'):
            #line = line[0:line.find(';')].strip()
        global isStopPrint, isGet, file_size
        isGet = True
        params = {"filename": GcodeFile.decode('utf8'), "printMode": 3, "printStartTime":int(time.time()), "printTotalSize":file_size, "printTotalTime":0, "materialLength":0 }
        #gcodeFile = open(GcodeFile, 'r')
        gcodeFile = open("/tmp/mounts/SD-P1/%s.g" % urllib.quote(GcodeFile).decode('utf8'), 'r')
        while True:
            line = gcodeFile.readline()
            if not line or isStopPrint:
                gcodeFile.close()
                break
            commentPos = line.find(';')
            if commentPos == -1:
                line = line[0:line.find(';')].strip()
                if len(line) > 0:
                    lineNr = lineNr+1
                    cur.execute("INSERT INTO Gcode(gcode, pos) VALUES (?, ?)", (line, gcodeFile.tell())) 
                    if lineNr%100 == 0:
                        sql.commit()
                    if isGet:
                        if line.startswith("G0") or line.startswith("G1"):
                            isGet = False
                            print("socket=>SliceParams" + json.dumps(params))
                            socketSend("SliceParams:" + json.dumps(params))
            else:
                if line.startswith(";id:"):
                    print(line[4:].strip())
                    params["id"] = line[4:].strip()
                elif line.startswith(";images:"):
                    print(line[8:].strip())
                    params["imageUrl"] = line[8:].strip()
                elif line.startswith(";Layer count:"):
                    print(line[13:].strip())
                    params["zMax"] = line[13:].strip()
                elif line.startswith(";settings:"):
                    try:
                        param = re.search(";settings:\\s*(\\w+):\\s*(.*)", line)
                        print(param.group(1), "=>", param.group(2))
                        key = param.group(1).strip()
                        value = param.group(2).strip()
                        if key == "suppor_surface":
                            params["supporSurface"] = value
                        elif key == "layer_height":
                            params["layerHeight"] = value
                        elif key == "support":
                            params["support"] = value
                        elif key == "platform_adhesion":
                            params["platformAdhesion"] = value
                        elif key == "bottom_layer_speed":
                            params["bottomLayerSpeed"] = value
                        elif key == "print_speed":
                            params["printSpeed"] = value
                        elif key == "retraction_amount":
                            params["retractionAmount"] = value
                        elif key == "retraction_speed":
                            params["retractionSpeed"] = value
                        elif key == "retraction_enable":
                            params["retractionEnable"] = value
                        elif key == "wall_count":
                            params["wallCount"] = value
                        elif key == "solid_layer_count":
                            params["solidLayerCount"] = value
                        elif key == "support_z_distance":
                            params["supportZDistance"] = value
                        elif key == "support_xy_distance":
                            params["supportXyDistance"] = value
                        elif key == "support_fill_rate":
                            params["supportFillRate"] = value
                        elif key == "support_angle":
                            params["supportAngle"] = value
                        elif key == "travel_speed":
                            params["travelSpeed"] = value
                        elif key == "fill_density":
                            params["fillDensity"] = value
                        elif key == "filament_diameter":
                            params["filamentDiameter"] = value
                    except:
                        pass
                elif line.startswith(";Sliced at:"):
                    #print(line[11:].strip())
                    #params["sliceTime"] = line[11:].strip()
                    param = re.search("(\\d{2}-\\d{2}-\\d{4} \\d{2}:\\d{2}:\\d{2})", line)
                    print(param.group(1))
                    params["sliceTime"] = str(int(time.mktime(time.strptime(param.group(1),'%d-%m-%Y %H:%M:%S'))))
                elif line.startswith(";Print time:"):
                    print(line[12:].strip())
                    params["printTotalTime"] = line[12:].strip()
                elif line.startswith(";Filament used:"):
                    try:
                        param = re.search(";Filament used:\\s*(.*) \\s*(.*)", line)
                        print(param.group(1), "=>", param.group(2))
                        params["materialLength"] = param.group(1).strip()
                        params["printTotalSize"] = param.group(2).strip()
                    except:
                        pass
        sql.commit()
        print("gcode convert db over!!!")
        #cur.execute('SELECT * FROM Gcode WHERE id=1')
        #print(cur.fetchone())
        #print(cur.fetchall())
        #for row in cur.execute('SELECT * FROM Gcode ORDER BY id'):
            #print(row)
        #cur.close()
        #sql.close()
        
    def convertList(self, _gcodeList, GcodeFile):
        print("gcode convert list to memory")
        global isGet
        isGet = True
        params = {"filename": GcodeFile.decode('utf8'), "printMode": 3, "printStartTime":int(time.time()), "printTotalSize":0, "printTotalTime":0, "materialLength":0 }
        #gcodeFile = open(GcodeFile, 'r')
        gcodeFile = open("/tmp/mounts/SD-P1/%s.g" % urllib.quote(GcodeFile), 'r')
        while True:
            line = gcodeFile.readline()
            if not line or isStopPrint:
                gcodeFile.close()
                break
            #line = line[0:line.find(';')].strip()
            #if len(line) > 0:
                #_gcodeList.append(line)
            commentPos = line.find(';')
            if commentPos == -1:
                line = line[0:line.find(';')].strip()
                if len(line) > 0:
                    _gcodeList.append(line)
                    if isGet:
                        if line.startswith("G0") or line.startswith("G1"):
                            isGet = False
                            print("socket=>SliceParams" + json.dumps(params))
                            socketSend("SliceParams:" + json.dumps(params))
            else:
                if line.startswith(";id:"):
                    print(line[4:].strip())
                    params["id"] = line[4:].strip()
                elif line.startswith(";images:"):
                    print(line[8:].strip())
                    params["imageUrl"] = line[8:].strip()
                elif line.startswith(";Layer count:"):
                    print(line[13:].strip())
                    params["zMax"] = line[13:].strip()
                elif line.startswith(";settings:"):
                    try:
                        param = re.search(";settings:\\s*(\\w+):\\s*(.*)", line)
                        print(param.group(1), "=>", param.group(2))
                        key = param.group(1).strip()
                        value = param.group(2).strip()
                        if key == "suppor_surface":
                            params["supporSurface"] = value
                        elif key == "layer_height":
                            params["layerHeight"] = value
                        elif key == "support":
                            params["support"] = value
                        elif key == "platform_adhesion":
                            params["platformAdhesion"] = value
                        elif key == "bottom_layer_speed":
                            params["bottomLayerSpeed"] = value
                        elif key == "print_speed":
                            params["printSpeed"] = value
                        elif key == "retraction_amount":
                            params["retractionAmount"] = value
                        elif key == "retraction_speed":
                            params["retractionSpeed"] = value
                        elif key == "retraction_enable":
                            params["retractionEnable"] = value
                        elif key == "wall_count":
                            params["wallCount"] = value
                        elif key == "solid_layer_count":
                            params["solidLayerCount"] = value
                        elif key == "support_z_distance":
                            params["supportZDistance"] = value
                        elif key == "support_xy_distance":
                            params["supportXyDistance"] = value
                        elif key == "support_fill_rate":
                            params["supportFillRate"] = value
                        elif key == "support_angle":
                            params["supportAngle"] = value
                        elif key == "travel_speed":
                            params["travelSpeed"] = value
                        elif key == "fill_density":
                            params["fillDensity"] = value
                        elif key == "filament_diameter":
                            params["filamentDiameter"] = value
                    except:
                        pass
                elif line.startswith(";Sliced at:"):
                    #print(line[11:].strip())
                    #params["sliceTime"] = line[11:].strip()
                    param = re.search("(\\d{2}-\\d{2}-\\d{4} \\d{2}:\\d{2}:\\d{2})", line)
                    print(param.group(1))
                    params["sliceTime"] = str(int(time.mktime(time.strptime(param.group(1),'%d-%m-%Y %H:%M:%S'))))
                elif line.startswith(";Print time:"):
                    print(line[12:].strip())
                    params["printTotalTime"] = line[12:].strip()
                elif line.startswith(";Filament used:"):
                    try:
                        param = re.search(";Filament used:\\s*(.*) \\s*(.*)", line)
                        print(param.group(1), "=>", param.group(2))
                        params["materialLength"] = param.group(1).strip()
                        params["printTotalSize"] = param.group(2).strip()
                    except:
                        pass
        
    def sendGcode(self, id):
        global isDiskMode
        if isDiskMode:
            #sqlite
            try:
                self.cur.execute('SELECT * FROM Gcode WHERE id='+str(id))
                return self.cur.fetchone()
            except:
                print("sqlite sendGcode None")
                return None
        else:
            #list
            try:
                global gcodeList, gcodeListSize, file_size
                #print(len(gcodeList))
                #print("serial=>",id, gcodeList[id-1])
                return [id, gcodeList[id-1], int(file_size*id/gcodeListSize)]
            except:
                print("gcodeList sendGcode None")
                return None
        
    def run(self):
        print("SerialThread start")    
        # serialSend("M117 SN:!"+getID())    
        global GcodeFile, _serial, isDiskMode, isStopPrint, TarTemp, CurTemp, PrintMode, CurrentZ, PrintReadSize, PrintTime, gcodeList, gcodeListSize, PrintLed, gcodePos
        #gcodeList = []
        gcodePos = 0
        try:
            self.sql = sqlite3.connect("/tmp/mounts/SD-P1/dayin.la.db", check_same_thread = False)
            self.cur = self.sql.cursor()
        except:
            traceback.print_exc()
        #threading.Thread(target = self.convertDb,args = (sql, GcodeFile)).start()
        while True:
            try:
                line = _serial.readline().strip()
                
                if GcodeFile != "":
                    #global isStopPrint
                    setDiskMode()
                    isStopPrint = False
                    PrintMode = 3
                    gcodePos = 0
                    PrintTime = int(time.time())
                    sendHeartBeat()
                    if isDiskMode:
                        threading.Thread(target = self.convertDb, args = (self.sql, GcodeFile)).start()
                    #else:
                        #threading.Thread(target = self.convertList, args = (gcodeList, GcodeFile)).start()
                    time.sleep(1)
                    print("printing==>")
                    serialSend(checksumGcode(0, "M110"))
                    GcodeFile = ""
                    PrintLed = 3
                            
                if len(line) > 0:
                    # print("serial<=", line)
                    if line.startswith('ok'):
                        if PrintMode > 0:
                            gcodePos = gcodePos+1
                            #print("gcodePos", gcodePos)
                            try:
                                #self.cur.execute('SELECT * FROM Gcode WHERE id='+str(gcodePos))
                                #one = self.cur.fetchone()
                                one = self.sendGcode(gcodePos)
                                if one != None:
                                    cmd = str(one[1])
                                    #print(cmd)
                                    #print("serial=>",one[0], cmd)
                                    #_serial.write(cmd+'\n')
                                    #socketSend(checksumGcode(int(one[0]), cmd))
                                    if 'M104' in cmd or 'M109' in cmd:
                                        try:
                                            TarTemp = int(re.search('S([0-9]+)', cmd).group(1))
                                        except:
                                            pass
                                        #serialSend(checksumGcode(int(one[0]), "M109 S200"))
                                        serialSend(checksumGcode(int(one[0]), cmd))
                                    elif 'Z' in cmd:
                                        try:
                                            CurrentZ = int(int(re.search('Z([0-9\.]*)', cmd).group(1)) / 0.15)
                                        except:
                                            pass
                                        serialSend(checksumGcode(int(one[0]), cmd))
                                    else:
                                        serialSend(checksumGcode(int(one[0]), cmd))
                                    PrintReadSize = int(one[2])
                                else:                            
                                    print("ok printed!!!")
                                    TarTemp = 0
                                    CurTemp = 0
                                    PrintMode = 0
                                    CurrentZ = 0
                                    PrintReadSize = 0
                                    PrintTime = 0
                                    #gcodeList = []
                                    #gcodeListSize = 0
                                    socketSend("StopPrint:")
                                    PrintLed = 2
                            except:
                                traceback.print_exc()
                        if ' T:' in line:
                            try:
                                CurTemp = float(re.search("T:([0-9\.]*)", line).group(1))
                                sendHeartBeat()
                                if CurTemp < 40:
                                    PrintLed = 1
                                elif CurTemp < TarTemp-3:
                                    PrintLed = 2
                                else:
                                    PrintLed = 3
                            except:
                                pass
                    elif line.startswith("Resend"):                        
                        gcodePos = int(line.split(":")[-1])
                        print("RS=>gcodePos", gcodePos)
                        try:
                            #self.cur.execute('SELECT * FROM Gcode WHERE id='+str(gcodePos))
                            #one = self.cur.fetchone()
                            one = self.sendGcode(gcodePos)
                            if one != None:
                                cmd = str(one[1])
                                # print(cmd)
                                #_serial.write(cmd+'\n')
                                serialSend(checksumGcode(int(one[0]), cmd))
                                PrintReadSize = int(one[2])
                            else:
                                print("Err Stop printed!!!")
                                serialSend("M117 Stop Print... Err")
                                TarTemp = 0
                                CurTemp = 0
                                PrintMode = 0
                                CurrentZ = 0
                                PrintReadSize = 0
                                PrintTime = 0
                                #gcodeList = []
                                #gcodeListSize = 0
                                socketSend("StopPrint:")
                                PrintLed = 2
                                #try:
                                    #global _socket
                                    #_socket.send("StopPrint:\n")
                                #except:
                                    #traceback.print_exc()
                        except:
                            traceback.print_exc()
                    elif line.startswith("wait"):
                        serialSend("M105")
                    elif ' T:' in line or line.startswith('T:'):
                        try:
                            CurTemp = float(re.search("T:([0-9\.]*)", line).group(1))
                            sendHeartBeat()
                            if CurTemp < 40:
                                PrintLed = 1
                            elif CurTemp < TarTemp-3:
                                PrintLed = 2
                            else:
                                PrintLed = 3
                        except:
                            pass
                    elif line.startswith("Error"):
                        if line.startswith("Error:Line Number is not Last Line Number+1"):                            
                            gcodePos = int(line.split(":")[-1])
                            print("Error:RS=>gcodePos", gcodePos)
                            serialSend(checksumGcode(gcodePos, "M110"))
                            # try:
                            #     #self.cur.execute('SELECT * FROM Gcode WHERE id='+str(gcodePos))
                            #     #one = self.cur.fetchone()
                            #     one = self.sendGcode(gcodePos)
                            #     if one != None:
                            #         cmd = str(one[1])
                            #         # print(cmd)
                            #         #_serial.write(cmd+'\n')
                            #         serialSend(checksumGcode(int(one[0]), cmd))
                            #         PrintReadSize = int(one[2])
                            #     else:
                            #         print("Err Stop printed!!!")
                            #         serialSend("M117 Stop Print... Err")
                            #         TarTemp = 0
                            #         CurTemp = 0
                            #         PrintMode = 0
                            #         CurrentZ = 0
                            #         PrintReadSize = 0
                            #         PrintTime = 0
                            #         #gcodeList = []
                            #         #gcodeListSize = 0
                            #         socketSend("StopPrint:")
                            #         PrintLed = 2
                            #         #try:
                            #             #global _socket
                            #             #_socket.send("StopPrint:\n")
                            #         #except:
                            #             #traceback.print_exc()
                            # except:
                            #     traceback.print_exc()
                        else:
                            print(line)
                    # else:
                        # socketSend("LOG:"+line)
            except:
                #traceback.print_exc()
                _serial.close()
                serialList = [] + glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
                if len(serialList)>0 :
                    _serial = serial.Serial(serialList[0], 115200, timeout=1)
        _serial.close()
        self.cur.close()
        self.sql.close()

if __name__ == '__main__':
    try:
        signal.signal(signal.SIGINT, quit)
        signal.signal(signal.SIGTERM, quit)
        
        getID()

        #CheckTimer()
        
        ledThread = LedThread()
        ledThread.setDaemon(True)
        ledThread.start()
        buttonThread = ButtonThread()
        buttonThread.setDaemon(True)
        buttonThread.start()
        serialThread = SerialThread()
        serialThread.setDaemon(True)
        serialThread.start()
        socketThread = SocketThread()
        socketThread.setDaemon(True)
        socketThread.start()        
        while True:
            pass
    except:
        traceback.print_exc()
        print("==============Force Quit==============")