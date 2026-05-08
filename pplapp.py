import datetime
import json
import threading
import asyncio
import os
from nats.aio.client import Client as NATS

class Pplapp:
    
    def __init__(self, ipAddress, username, password):
        self.measurements = {}
        self.ipAddress = ipAddress
        self.username = username
        self.password = password
        self.connectToNats = True
        self.connection = None
        self._sub = None
        self._lock = asyncio.Lock()
        self._loop = None

        threading.Thread(target=self._run_loop, daemon=True).start()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self.natsConnect())

    def stop(self):
        self.connectToNats = False
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.natsDisconnect(), self._loop)

    async def _cleanupStaleConnection(self):
        async with self._lock:
            if self.connection is None:
                return
            self.connection = None
            self._sub = None
        print("NATS connection lost. Will retry...")

    async def natsConnect(self):
        while self.connectToNats:
            nc = NATS()

            async def _on_disconnect():
                await self._cleanupStaleConnection()

            async def _on_closed():
                await self._cleanupStaleConnection()

            async def _on_error(e):
                print(f"NATS error: {e}")
                await self._cleanupStaleConnection()

            try:
                await asyncio.wait_for(
                    nc.connect(
                        servers=[f"nats://{self.ipAddress}:4222"],
                        user=self.username,
                        password=self.password,
                        allow_reconnect=False,
                        connect_timeout=5,
                        max_reconnect_attempts=0,
                        disconnected_cb=_on_disconnect,
                        closed_cb=_on_closed,
                        error_cb=_on_error,
                    ),
                    timeout=15,
                )

                async with self._lock:
                    self.connection = nc
                    self._sub = await nc.subscribe("nats_dialog", cb=self.processMessage)

                print("Successfully connected to NATS server")
                await self.sendMessageAsync("request", "reportMeasurements", "all", "1")

                # Stay alive while connected
                while self.connectToNats and self.connection is not None and nc.is_connected:
                    await asyncio.sleep(1)

                # If we exited because connection dropped, loop will retry
                # If we exited because connectToNats is False, fall through and disconnect
                if not self.connectToNats:
                    await self.natsDisconnect()
                    return

                # Connection dropped — small backoff before retry
                print("Connection dropped, retrying in 5 seconds...")
                await asyncio.sleep(5)

            except asyncio.TimeoutError:
                print("Error connecting to NATS server: Timeout error. Retrying in 5 seconds...")
                try:
                    if nc.is_connected:
                        await nc.close()
                except Exception:
                    pass
                await asyncio.sleep(5)
            except Exception as e:
                print(f"Error connecting to NATS server: {e}. Retrying in 5 seconds...")
                try:
                    if nc.is_connected:
                        await nc.close()
                except Exception:
                    pass
                await asyncio.sleep(5)

    async def natsDisconnect(self):
        nc_to_close = None
        sub_to_unsub = None
        async with self._lock:
            if self.connection is not None:
                nc_to_close = self.connection
                sub_to_unsub = self._sub
                self.connection = None
                self._sub = None
            else:
                return
        if sub_to_unsub:
            try:
                await sub_to_unsub.unsubscribe()
            except Exception:
                pass
        if nc_to_close:
            try:
                await nc_to_close.close()
            except Exception as e:
                print(f"Error disconnecting from NATS server: {e}")

    async def processMessage(self, messageRaw):
        try:
            message = json.loads(messageRaw.data.decode())
            messageType = message.get("msg_type", "0")
            messageId = message.get("msg_id", "0")
            deviceId = message.get("device_id", "0")
            payload = message.get("payload", "0")

            if messageType == "reply" and messageId == "reportMeasurements" and deviceId == "all" and isinstance(payload, dict):
                self.writeMeasurements(payload)
            if messageType == "reply" and messageId == "getLogs":
                self.saveLogFile(payload)
        except Exception as e:
            print(f"Error processing message: {e}")

    async def sendMessageAsync(self, messageType, messageId, deviceId, command):
        try:
            message = {
                "timestamp": self.__getCurrentTimestamp(),
                "msg_type": messageType,
                "msg_id": messageId,
                "device_id": deviceId,
                "command": command
            }
            await self.connection.publish("nats_dialog", json.dumps(message).encode())
        except Exception as e:
            print(f"Error sending message: {e}")

    def __getCurrentTimestamp(self):
        currentTime = datetime.datetime.utcnow()
        return currentTime.strftime("[%Y.%m.%d_%H:%M:%S]")

    def writeMeasurements(self, payload):
        try:
            for deviceId, measurements in payload.items():
                if not self.__deviceExists(deviceId):
                    self.measurements[deviceId] = {}
                self.measurements[deviceId].update(measurements)
        except Exception as e:
            print(f"Error writing measurements: {e}")

    def saveLogFile(self, content):
        lines = content.splitlines()

        filename = lines[0].split('/')[-1]
        logContent = "\n".join(lines[1:])

        logDirectory = 'logs'

        if not os.path.exists(logDirectory):
            os.makedirs(logDirectory)

        filepath = os.path.join(logDirectory, filename)

        with open(filepath, 'w') as file:
            file.write(logContent)

    def __deviceExists(self, deviceId):
        return deviceId in self.measurements
    
    def getAllMeasurements(self):
        return self.measurements

    def getMeasurements(self, deviceId, measurement):
        return self.measurements.get(deviceId, {}).get(measurement)
    
    def getLogs(self):
        self.sendMessage("request", "getLogs", "", "")
        
    def sendTelegram(self, message, level="INFO"):
        self.sendMessage("request", "sendTelegram", message, level)
        
    def setCommands(self, deviceId, commands):
        self.sendMessage("request", "setCommands", deviceId, commands)

    def sendMessage(self, messageType, messageId, deviceId, commands):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.sendMessageAsync(messageType, messageId, deviceId, commands), self._loop)
        else:
            threading.Thread(target=asyncio.run, args=(self.sendMessageAsync(messageType, messageId, deviceId, commands),)).start()
