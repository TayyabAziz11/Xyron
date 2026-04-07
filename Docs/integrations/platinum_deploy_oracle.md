# Platinum Deploy Guide — Oracle Free Tier VM
**Date:** 2026-02-27
**Target:** Oracle Cloud Free Tier (ARM or AMD instances)

---

## 1. Oracle Free Tier VM Specifications

| Resource | Free Tier Limit |
|----------|----------------|
| Ampere A1 ARM | 4 OCPUs + 24 GB RAM (shared across up to 4 instances) |
| AMD VM.Standard.E2.1.Micro | 2 instances (1 GB RAM each) |
| Block Storage | 200 GB total |
| Outbound bandwidth | 10 TB/month |

**Recommendation:** Use one **Ampere A1** instance (2 OCPU, 12 GB RAM) for the cloud worker.

---

## 2. VM Setup

### 2.1 Create Instance
1. Sign into https://cloud.oracle.com
2. Compute → Instances → Create Instance
3. Image: Ubuntu 22.04 Minimal (ARM)
4. Shape: VM.Standard.A1.Flex (2 OCPU, 12 GB RAM)
5. Add your SSH public key
6. Download the private key if generated

### 2.2 Initial Setup (SSH into VM)
```bash
ssh -i ~/.ssh/oracle_key ubuntu@<your-vm-ip>

# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install dependencies
sudo apt-get install -y python3.11 python3.11-pip python3.11-venv git nodejs npm

# Install PM2
sudo npm install -g pm2

# Verify
python3.11 --version && pm2 --version
```

---

## 3. Deploy the AI Employee Vault (Cloud)

### 3.1 Clone Repository
```bash
cd ~
git clone https://github.com/<your-username>/personal-ai-employee.git vault
cd vault
```

### 3.2 Create Python Virtual Environment
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3.3 Configure Environment Variables
```bash
# Create .env (NEVER commit this file)
cat > .env << 'EOF'
# Gmail OAuth credentials
GMAIL_CLIENT_ID=your_client_id
GMAIL_CLIENT_SECRET=your_client_secret
GMAIL_REFRESH_TOKEN=your_refresh_token

# Anthropic API key (for Claude)
ANTHROPIC_API_KEY=your_api_key

# Git identity for cloud commits
GIT_AUTHOR_NAME="Cloud Worker"
GIT_AUTHOR_EMAIL="cloud@personal-ai-employee.local"

# Cloud worker settings
MAX_PENDING_APPROVALS=10
LOOP_SECONDS=60
EOF

chmod 600 .env
```

### 3.4 Configure Git for Cloud Commits
```bash
git config user.name "Cloud Worker"
git config user.email "cloud@personal-ai-employee.local"

# For HTTPS auth without password prompts (use a GitHub Personal Access Token)
git remote set-url origin https://<PAT>@github.com/<username>/personal-ai-employee.git
# OR use SSH keys (recommended):
# ssh-keygen -t ed25519 -C "oracle-cloud-worker"
# Add ~/.ssh/id_ed25519.pub to GitHub Deploy Keys
```

---

## 4. PM2 Configuration

### 4.1 Start Cloud Worker with Platinum Config
```bash
pm2 start ecosystem.platinum.cloud.config.cjs

# Check status
pm2 list
pm2 logs cloud-worker-orchestrator

# Save PM2 process list for startup
pm2 save
```

### 4.2 Enable PM2 on Boot
```bash
pm2 startup systemd -u ubuntu --hp /home/ubuntu
# Run the command it outputs, e.g.:
sudo env PATH=$PATH:/usr/bin pm2 startup systemd -u ubuntu --hp /home/ubuntu
pm2 save
```

---

## 5. Ports & Networking

| Service | Port | Direction | Purpose |
|---------|------|-----------|---------|
| SSH | 22 | Inbound | Admin access |
| Odoo | 8069 | Inbound (restricted) | Odoo web UI |
| HTTPS (optional) | 443 | Inbound | Nginx reverse proxy |

### Oracle Security List (VCN)
```
# Add inbound rule for Odoo (restrict to your IP)
Protocol: TCP
Source: <your-home-ip>/32
Dest Port: 8069
```

---

## 6. Odoo Community Deployment (for Platinum PT-6)

### 6.1 Install Odoo 17/18
```bash
sudo apt-get install -y postgresql postgresql-client
sudo -u postgres createuser -d -R -S odoo
sudo -u postgres createdb -O odoo odoodev

# Install Odoo dependencies
sudo apt-get install -y python3-pip python3-dev libpq-dev

# Download and install Odoo (community edition)
wget https://nightly.odoo.com/18.0/nightly/deb/odoo_18.0_latest_all.deb
sudo dpkg -i odoo_18.0_latest_all.deb
sudo apt-get -f install

# Configure
sudo nano /etc/odoo/odoo.conf
# Set: db_password, admin_passwd, workers=2, max_cron_threads=1
```

### 6.2 Configure Systemd for Odoo
```bash
sudo systemctl enable odoo
sudo systemctl start odoo
sudo systemctl status odoo
```

### 6.3 Nginx Reverse Proxy (HTTPS)
```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx

# Create nginx config
sudo nano /etc/nginx/sites-available/odoo
# (standard Odoo nginx config with proxy_pass http://127.0.0.1:8069)

sudo certbot --nginx -d odoo.yourdomain.com
```

---

## 7. Log Rotation

**Important:** Oracle Free VMs have limited disk space. Configure log rotation.

```bash
# Add to /etc/logrotate.d/pm2-cloud-worker
sudo tee /etc/logrotate.d/pm2-cloud-worker << 'EOF'
/home/ubuntu/.pm2/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF

# Test logrotate
sudo logrotate --debug /etc/logrotate.d/pm2-cloud-worker
```

**PM2 log rotation plugin:**
```bash
pm2 install pm2-logrotate
pm2 set pm2-logrotate:max_size 10M
pm2 set pm2-logrotate:retain 7
pm2 set pm2-logrotate:compress true
```

---

## 8. Health Monitoring

### 8.1 Service Ready Signals
Each cloud service prints `SERVICE_READY` on startup and touches `/tmp/<service>.ready`.

Check health:
```bash
cat /tmp/cloud-worker-orchestrator.ready
# Expected: file exists and contains recent timestamp

pm2 list | grep "cloud-worker"
# Expected: status = online, uptime > 0
```

### 8.2 Simple Uptime Check (cron)
```bash
# Add to crontab (crontab -e)
*/5 * * * * /home/ubuntu/vault/scripts/demo/check_health.sh >> /tmp/health.log 2>&1
```

---

## 9. Security Checklist

- [ ] `.env` file: permissions 600 (only owner readable)
- [ ] `.secrets/` directory: in `.gitignore`
- [ ] SSH key: use Ed25519, disable password auth
- [ ] GitHub PAT: use fine-grained token, repo access only
- [ ] Odoo admin password: strong random string
- [ ] Oracle Security List: whitelist your home IP for port 8069
- [ ] Firewall: `sudo ufw enable && sudo ufw allow 22 && sudo ufw allow 443`

---

## 10. Troubleshooting

| Issue | Check |
|-------|-------|
| Cloud worker not starting | `pm2 logs cloud-worker-orchestrator` |
| Git push fails | Check PAT expiry; verify remote URL |
| No items being claimed | Check `Needs_Action/email/` has files; check `--dry-run` not set |
| Approval flood halt | Check `In_Progress/cloud/` count; review `--max-pending-approvals` |
| Python import error | `source .venv/bin/activate` before running |
| Odoo unreachable | `sudo systemctl status odoo`, check port 8069 firewall rule |
