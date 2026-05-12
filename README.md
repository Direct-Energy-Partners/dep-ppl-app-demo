# dep-ppl-app

Repository for developing custom applications for the PPL controller.

## Repository Structure

```
├── app/
│   ├── __main__.py      # Allows running the app with `python -m app`
│   └── main.py          # Main entry point – edit this to implement your application
├── examples/            # Example applications for reference
├── pplapp.py            # PPL controller interface (NATS connection, measurements, commands)
├── .env                 # Environment variables (IP address, NATS credentials)
├── requirements.txt     # Python dependencies
├── install.sh           # One-time setup script for the controller
├── start.sh             # Start the app
└── stop.sh              # Stop the app
```

## Getting Started

### 1. Configure the `.env` File

Update the `.env` file with the IP address of your PPL controller and the NATS credentials:

```env
IP_ADDRESS="192.168.1.10"
NATS_USERNAME="admin"
NATS_PASSWORD="password"
```

### 2. Install Dependencies

Make sure all required Python packages are installed:

```bash
pip install -r requirements.txt
```

### 3. Implement Your Application

Edit `app/main.py` to implement your custom logic. The file already contains a working skeleton that:

- Connects to the PPL controller via NATS
- Receives measurements in a loop
- Logs device states

You can use any of the methods provided by the `Pplapp` class in `pplapp.py`:

- **`getAllMeasurements()`** – Returns all measurements from all devices.
- **`getMeasurements(deviceId, measurement)`** – Returns a specific measurement for a device.
- **`setCommands(deviceId, commands)`** – Sends commands to a device.
- **`sendTelegram(message, level)`** – Sends a Telegram notification.
- **`getLogs()`** – Downloads logs from the controller.

Check the `examples/` folder for reference implementations.

### 4. Run Locally

Run the app locally to test your changes:

```bash
python -m app
```

## Deploying to the PPL Controller

Once you have tested your application locally, deploy it to the PPL controller. The app should be placed in the `/opt/plcnext/appshome/data` folder on the controller.

### Option A: Copy with FileZilla

Use an SFTP client like FileZilla to copy the entire `dep-ppl-app` folder to `/opt/plcnext/appshome/data` on the controller.

### Option B: Git Clone (Recommended)

> **Note:** The controller needs access to the internet for this option.

SSH into the controller, navigate to the target folder, and clone the repository directly:

```bash
cd /opt/plcnext/appshome/data
git clone <your-repo-url>
```

The benefit of this approach is that any future changes you push can be pulled directly onto the controller:

```bash
cd /opt/plcnext/appshome/data/dep-ppl-app
git pull
```

### Install on the Controller

After copying or cloning the repo onto the controller, run the install script **once**:

```bash
bash install.sh
```

This will:

1. Create a Python virtual environment (`.venv`)
2. Install all dependencies from `requirements.txt`
3. Register a cron job that automatically starts the app on every reboot of the controller

### Start and Stop the App

You can manually start or stop the app at any time:

```bash
bash start.sh   # Start the app
bash stop.sh    # Stop the app
```

After running `install.sh`, the app will also start automatically whenever the controller reboots.
