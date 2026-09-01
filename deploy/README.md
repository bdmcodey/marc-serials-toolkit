# Deploying the web tools

Hosts the **Holdings Workbench** (`/workbench`), the **Converter**
(`/converter`) and the **Pattern Detector** (`/patterns`). The workbench imports
the other two apps' engines rather than duplicating them, so all three are
served from one checkout and one virtualenv. The PNX Lookup and AI tools are not
hosted (run locally).

## Placeholders

These files are written for whoever is deploying, not for one particular server.
Substitute throughout before installing anything:

| Placeholder | Replace with |
|---|---|
| `tools.example.com` | the hostname you are serving from |
| `youruser` | the Linux account the services run as |
| `/home/youruser/marc-serials-toolkit` | wherever you cloned the repository |
| `CHANGE_ME_RANDOM_HEX` | a fresh secret per service (step 3) |

## First install

On a server with none of this running yet.

### 1. DNS
In your DNS provider, add an **A record** pointing `tools` at your server's
IP address (TTL 300).

### 2. Clone + venv (on the server)
```bash
cd ~
git clone https://github.com/bdmcodey/marc-serials-toolkit.git
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
sudo certbot --nginx -d tools.example.com          # choose Redirect
```

Certbot does not write the `:443` block from scratch — it adds `listen 443 ssl`
to the block it finds and moves the plain `:80` listener into a new redirect
block underneath. Everything in this file, the rate limits included, is carried
across because it is the same block. Confirm that it was:

```bash
sudo grep -c 'limit_req zone=tools' /etc/nginx/sites-available/tools
```

Expect **3**, one per proxied tool. A `0` means certbot did not carry the
locations over and the `limit_req` lines need adding to the `:443` block by
hand. Worth checking because rate limiting fails open: nothing about a working
site tells you the limits are gone.

The zone itself is defined once, at the top of this file, which is http context
because nginx includes `sites-enabled/*` from inside its `http` block. It does
not need a separate file in `/etc/nginx/conf.d/` — and must not also be defined
there, or nginx will refuse to start on a duplicate zone name.

### 5. Landing page
`/` serves a page listing the three tools. It is a single static file with no
app behind it, but nginx runs as `www-data` and usually cannot traverse a home
directory, so it is copied out of the checkout rather than read from it:

```bash
sudo mkdir -p /var/www/tools
sudo cp deploy/landing/index.html /var/www/tools/
```

Re-copy it whenever `deploy/landing/index.html` changes; nothing needs
restarting, as nginx reads it per request.

### 6. Verify
- https://tools.example.com/ — the landing page
- https://tools.example.com/workbench
- https://tools.example.com/converter
- https://tools.example.com/patterns

## Updating a server that is already running

**Do not follow the first-install steps on a live server.** Two of them will
break it, both for the same underlying reason: what is in this repository is a
template, while what is on the server has been filled in — by you in the case of
the units, and by certbot in the case of nginx.

> **Never re-copy the systemd units.** The live ones carry your real account
> name and a real `SECRET_KEY`. The copies here carry `youruser` and
> `CHANGE_ME_RANDOM_HEX`, so copying them over stops the services from starting.
> Units only need replacing when this repository actually changes one, which is
> rare and will be called out in the changelog.

> **Never re-copy `nginx-tools.conf`.** It is the port 80 block only. Running
> `certbot --nginx` rewrote the live file: it added a `:443` block holding all
> the real locations and turned the `:80` block into a redirect. Copying this
> file over that discards your TLS configuration and takes the site down. Edit
> the live file by hand instead.

### An ordinary update

Nothing structural has changed; new code, same three services.

```bash
cd ~/marc-serials-toolkit && git pull
sudo systemctl restart mcsite-converter mcsite-patterns mcsite-workbench
```

If the landing page changed, copy it across as well — it is not served from the
checkout:

```bash
sudo cp ~/marc-serials-toolkit/deploy/landing/index.html /var/www/tools/
```

Then check the version badge in the corner of any of the three tools. A release
that changes what a cataloguer sees says so in `shared/about.json`, which is
what the badge opens — worth reading before converting anything you care about,
because the converter's output can change between versions.

### Adding a service that was not there before

The workbench arrived this way, on a server already running the other two. Only
the new unit is installed; the existing ones are left alone.

```bash
cd ~/marc-serials-toolkit && git pull
```

Check whether the new service needs anything the virtualenv does not already
have. The workbench needed nothing: it imports the other two apps' engines, so
`flask` and `pymarc` were already installed.

Generate a secret for it — one per service, never shared:

```bash
python3 -c "import secrets;print(secrets.token_hex(32))"
```

Install and fill in that one unit. Substitute `youruser` throughout (it appears
in `User=`, `WorkingDirectory=` and `ExecStart=`) and paste the secret:

```bash
sudo cp deploy/mcsite-workbench.service /etc/systemd/system/ && sudo nano /etc/systemd/system/mcsite-workbench.service
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now mcsite-workbench && sudo systemctl status mcsite-workbench --no-pager
```

Add its location to the **`:443`** block of the live nginx file — the `:80`
block is only a redirect, so putting it there does nothing:

```bash
sudo nano /etc/nginx/sites-available/tools
```

```nginx
location = /workbench { return 301 /workbench/; }
location /workbench/ {
    limit_req zone=tools burst=20 nodelay;
    proxy_pass http://127.0.0.1:8003/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Test before reloading. `nginx -t` catches a mistyped block while the old
configuration is still serving; reloading without it does not:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Finally restart the other services, because a release usually changes them too:

```bash
sudo systemctl restart mcsite-converter mcsite-patterns
```

Add it to the landing page too, so the new tool is reachable from `/`:

```bash
sudo cp ~/marc-serials-toolkit/deploy/landing/index.html /var/www/tools/
```

Then check all three respond, as in **Verify** above, and try the new one with a
small `.mrc` — reaching a page proves nginx and the unit, not that the app works.

Re-check the rate limits too, since you have just edited the file that holds
them:

```bash
sudo grep -c 'limit_req zone=tools' /etc/nginx/sites-available/tools
```

Expect one per proxied tool — **4** once a fourth is added, and so on. It is
also worth running after an `nginx` package upgrade, which can offer to replace
this file.

### If something is wrong

```bash
sudo systemctl status mcsite-workbench --no-pager && sudo journalctl -u mcsite-workbench -n 40 --no-pager
```

A service that starts and immediately stops is usually an unsubstituted
placeholder in its unit — `youruser` has no home directory to run from.

## Hardening

The account name these units run as is in this repository's history, and the
hostname and address are in public DNS, so neither is a secret. Password
authentication over SSH plus a known account name is worth defending:

```bash
sudo apt install fail2ban && sudo systemctl enable --now fail2ban
```

Key-only authentication is stronger still (`PasswordAuthentication no` in
`/etc/ssh/sshd_config`), once you have a key on the server and have confirmed it
works from a second session.
