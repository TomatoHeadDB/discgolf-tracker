# Disc Golf Tracker

A small Flask web app to track the discs you own and preview an approximate
flight path for each one, generated from its Speed/Glide/Turn/Fade numbers.

- Multi-user accounts (register/login) — each person only sees their own bag
- SQLite by default; swap in Postgres later by setting `DATABASE_URL`
- Flight path is a visual approximation calibrated to typical throwing
  distances, not a real physics simulation

---

## 1. Create the Proxmox LXC

On the Proxmox host (or via the web UI):

1. Download a Debian 12 template if you don't have one:
   `pveam update && pveam available | grep debian-12`
   `pveam download local debian-12-standard_12.x-x_amd64.tar.zst`
2. Create the container (adjust IDs/sizes as needed):
   ```
   pct create 200 local:vztmpl/debian-12-standard_12.x-x_amd64.tar.zst \
     --hostname discgolf \
     --cores 1 --memory 512 --swap 512 \
     --rootfs local-lvm:4 \
     --net0 name=eth0,bridge=vmbr0,ip=dhcp \
     --unprivileged 1 \
     --features nesting=1
   pct start 200
   ```
3. Enter the container:
   ```
   pct enter 200
   ```

## 2. Install dependencies inside the LXC

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip git
```

## 3. Get the app onto the container

From your workstation, copy the `discgolf-tracker` folder into the
container (replace `200` with your container ID):

```bash
pct push 200 discgolf-tracker.tar.gz /root/discgolf-tracker.tar.gz
```
Then inside the container:
```bash
cd /root && tar xzf discgolf-tracker.tar.gz
```

(Or `git clone` it if you push this to your own git remote instead.)

## 4. Set up the Python environment

```bash
cd /root/discgolf-tracker
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

## 5. Configure secrets

```bash
cp .env.example .env
nano .env   # set SECRET_KEY to a long random string
```

Generate a random key quickly with:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## 6. Quick test run

```bash
./venv/bin/python app.py
```
Visit `http://<container-ip>:5000` from a browser on your network. Stop it
with Ctrl+C once you've confirmed it loads — the next step runs it properly
as a background service.

## 7. Run it as a systemd service (production)

Create `/etc/systemd/system/discgolf.service`:

```ini
[Unit]
Description=Disc Golf Tracker
After=network.target

[Service]
User=root
WorkingDirectory=/root/discgolf-tracker
Environment="PATH=/root/discgolf-tracker/venv/bin"
EnvironmentFile=/root/discgolf-tracker/.env
ExecStart=/root/discgolf-tracker/venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:
```bash
systemctl daemon-reload
systemctl enable --now discgolf
systemctl status discgolf
```

The app is now reachable at `http://<container-ip>:5000` and will survive
reboots.

## 8. (Optional) Put it behind Nginx on port 80

```bash
apt install -y nginx
```

`/etc/nginx/sites-available/discgolf`:
```nginx
server {
    listen 80;
    server_name discgolf.local;   # or the container's IP

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
```bash
ln -s /etc/nginx/sites-available/discgolf /etc/nginx/sites-enabled/
rm /etc/nginx/sites-enabled/default
systemctl restart nginx
```

Now the app is on `http://<container-ip>/` (port 80).

## Updating later

```bash
cd /root/discgolf-tracker
# copy over new files, then:
./venv/bin/pip install -r requirements.txt
systemctl restart discgolf
```

## Notes on the flight path model

Each throw is plotted using a simplified curve driven by the disc's flight
numbers:
- **Speed/Glide** scale the overall distance
- **Turn** bends the flight early (high-speed phase)
- **Fade** hooks it back at the end (as the disc slows down)

It's tuned to *look* right and track relative differences between discs well
(an overstable putter vs. a turnover driver, etc.), not to predict your
exact throw. The flight-detail page lets you flip between RHBH/RHFH/LHBH/LHFH
and adjust a power slider to see how the shape scales.
