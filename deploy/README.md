# Deploying the web tools to tools.matthewcodey.com

Hosts the **Holdings Workbench** (`/workbench`), the **Converter**
(`/converter`) and the **Pattern Detector** (`/patterns`). The workbench imports
the other two apps' engines rather than duplicating them, so all three are
served from one checkout and one virtualenv. The PNX Lookup and AI tools are not
hosted (run locally).

### 1. DNS
In the Linode DNS manager, add an **A record**: `tools` → `173.255.215.18` (TTL 300).

### 2. Clone + venv (on the server)
```bash
cd ~
git clone https://github.com/bdmcodey/marc-serials-toolkit.git   # private: use a token/deploy key
cd marc-serials-toolkit
python3 -m venv .venv
. .venv/bin/activate
pip install flask pymarc gunicorn
deactivate
```

### 3. systemd services
```bash
# generate a random secret for each service and paste into the unit files:
python3 -c "import secrets;print(secrets.token_hex(32))"

sudo cp deploy/mcsite-converter.service deploy/mcsite-patterns.service \
        deploy/mcsite-workbench.service /etc/systemd/system/
sudo nano /etc/systemd/system/mcsite-converter.service   # set SECRET_KEY
sudo nano /etc/systemd/system/mcsite-patterns.service    # set a different SECRET_KEY
sudo nano /etc/systemd/system/mcsite-workbench.service   # set a third SECRET_KEY
sudo systemctl daemon-reload
sudo systemctl enable --now mcsite-converter mcsite-patterns mcsite-workbench
sudo systemctl status mcsite-converter mcsite-patterns mcsite-workbench --no-pager
```

### 4. nginx + TLS
```bash
sudo cp deploy/nginx-tools.conf /etc/nginx/sites-available/tools
sudo ln -sf /etc/nginx/sites-available/tools /etc/nginx/sites-enabled/tools
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d tools.matthewcodey.com     # choose Redirect
```

### 5. Verify
- https://tools.matthewcodey.com/workbench
- https://tools.matthewcodey.com/converter
- https://tools.matthewcodey.com/patterns

`/` redirects to `/workbench/`.

### Updating later
```bash
cd ~/marc-serials-toolkit && git pull
sudo systemctl restart mcsite-converter mcsite-patterns mcsite-workbench
```
