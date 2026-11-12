# نصب سریع روی لینوکس تازه — از صفر تا کار کردن

> فرض: لینوکس تازه نصب شده و **هیچ چیزی** رویش نیست. فقط یوزری داری که `sudo` دارد.
> همهٔ دستورها را کپی کن و پشت سر هم بزن. حدود ۵ دقیقه.

---

## ۰) اول: کدام حالت را انتخاب کنم؟

| وضعیت تو | حالت | چرا |
|---|---|---|
| لینوکس معمولی: لپ‌تاپ، دسکتاپ، سرور شخصی، VPS | **حالت ۱ — تونل (tunnel)** | WARP مثل یک VPN واقعی کل سیستم را می‌برد؛ چرخش خودکار بی‌دردسر کار می‌کند |
| GitHub Codespaces، Docker، devcontainer، CI، ماشینی که `/dev/net/tun` ندارد یا حق تغییر فایروال/روتینگ را نداری | **حالت ۲ — پروکسی (proxy)** | هیچ روتی عوض نمی‌شود، هیچ فایروالی لمس نمی‌شود؛ فقط دو پورت محلی باز می‌شود |

اگر شک داری: `ls /dev/net/tun` — اگر خطا داد، **حالت ۲**.

---

## گام مشترک ۱ — پیش‌نیازهای پایه (Ubuntu / Debian / Mint)

```bash
sudo apt update
sudo apt install -y curl unzip python3 ca-certificates gpg
```

> روی Fedora / RHEL: `sudo dnf install -y curl unzip python3` و بستهٔ WARP را از
> <https://pkg.cloudflareclient.com/> بگیر. بقیهٔ گام‌ها یکسان است.

---

## گام مشترک ۲ — نصب کلاینت Cloudflare WARP

```bash
curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
  | sudo gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp.gpg

echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp.gpg] https://pkg.cloudflareclient.com/ $(. /etc/os-release; echo $VERSION_CODENAME) main" \
  | sudo tee /etc/apt/sources.list.d/cloudflare-client.list

sudo apt update && sudo apt install -y cloudflare-warp
warp-cli --version        # باید نسخه چاپ شود
```

---

## گام مشترک ۳ — نصب opencode (اگر نداری)

```bash
curl -fsSL https://opencode.ai/install | bash
source ~/.bashrc
opencode --version
```

---

## گام مشترک ۴ — زیپ پروژه

```bash
cd ~
unzip opencode-unlimited-2.2.0.zip
cd opencode-unlimited-fixed
```

---

# حالت ۱ — لینوکس معمولی (تونل)

**کِی:** روی سیستم خودت، سرور، یا VPS؛ جایی که WARP می‌تواند VPN کل سیستم باشد.

### ۱. سرویس WARP را روشن و ثبت‌نام کن
```bash
sudo systemctl enable --now warp-svc
warp-cli --accept-tos registration new
warp-cli --accept-tos mode warp
warp-cli --accept-tos connect
warp-cli --accept-tos status          # باید بنویسد: Status update: Connected
```

### ۲. مطمئن شو ترافیک واقعاً از تونل می‌رود
```bash
curl -s https://www.cloudflare.com/cdn-cgi/trace | grep -E '^(ip|warp)='
# خروجی باید warp=on و ip= شروع‌شده با 2a09:bac… باشد
```

### ۳. نصب ابزار
```bash
sudo bash install.sh
```

### ۴. تست نهایی
```bash
ouc status      # باید: supervisor: running + warp tunnel: Connected + egress: 2a09:…
ouc verify      # یعنی: یک درخواست واقعی زده شد و جواب گرفت
```

**تمام.** از این به بعد خودش کار می‌کند: قفل سهمیه را در لاگ opencode می‌بیند، بلوک را عوض می‌کند و با یک درخواست واقعی تأیید می‌کند.
بعداً فقط `opencode` را مثل همیشه اجرا کن.

---

# حالت ۲ — پروکسی (Codespaces / Docker / CI)

**کِی:** داخل کانتینر یا هر جایی که TUN نداری یا نباید روت/فایروال عوض شود.

### ۱. سرویس WARP را روشن کن (بدون VPN شدن کل سیستم)
```bash
sudo nohup /usr/bin/warp-svc >/tmp/warp-svc.log 2>&1 &
warp-cli --accept-tos registration new
warp-cli --accept-tos mode proxy
warp-cli --accept-tos proxy port 40000
warp-cli --accept-tos connect
```

### ۲. نصب ابزار در حالت پروکسی
```bash
sudo bash install.sh --proxy --portable
```

### ۳. تست نهایی
```bash
ouc status      # باید: warp mode: proxy (no TUN) + http bridge: http://127.0.0.1:8080
ouc doctor
ouc verify
```

### ۴. اجرای opencode از داخل تونل — یکی از دو راه
```bash
# راه الف) خودکار و مطمئن (توصیه‌شده)
ouc lane run "سلام، خودت را معرفی کن"

# راه ب) دستی، با تنظیم پروکسی
export HTTPS_PROXY=http://127.0.0.1:8080 HTTP_PROXY=http://127.0.0.1:8080
opencode run "سلام، خودت را معرفی کن"
```

> اگر `HTTPS_PROXY` را ندهی، opencode مستقیم بیرون می‌رود و IP مشترک کانتینر را می‌بیند — یعنی چرخش هیچ فایده‌ای ندارد.

---

## اگر جایی گیر کردی

| نشانه | کار |
|---|---|
| `registration new` خطا داد | `warp-cli --accept-tos registration delete` → ۱۰ ثانیه صبر → دوباره `registration new` |
| `opencode: command not found` | `source ~/.bashrc` یا ترمینال را ببند و باز کن |
| `ouc: command not found` | `hash -r` (مسیر `/usr/local/bin` تازه اضافه شده) |
| `ouc hop` می‌گوید supervisor نیست | `sudo ouc start` یا `sudo systemctl start opencode-unlimited` |
| بعد از hop باز هم «Free usage limit» | `ouc hop` دوباره؛ حدود **۱ از ۳** بلوک‌ها از قبل مصرف شده‌اند — با `ouc verify` واقعیت را ببین، نه با تغییر آدرس |
| هیچ‌چیز کار نمی‌کند | `sudo ouc rescue` (دکمهٔ اضطراری: پاک‌سازی فایروال + اتصال مجدد WARP) |
| IPv6 نداری (`ip -6 addr show scope global` خالی است) | در حالت پروکسی چرخش نمی‌تواند سهمیه را عوض کند؛ `ouc doctor` این را صریح می‌گوید |

## حذف کامل
```bash
sudo bash uninstall.sh
```
opencode و سشن‌هایت و بستهٔ WARP دست‌نخورده می‌مانند.
