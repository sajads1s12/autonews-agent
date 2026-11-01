# 🔐 دليل GitHub Secrets — كل سرّ، ولماذا، وكيف

> **القاعدة الواحدة:** لا تضع أي قيمة في أي ملف. كل شيء عبر
> `Settings → Secrets and variables → Actions → New repository secret`.

---

## ١) الإلزامي — ٣ أسرار فقط

| # | الاسم | القيمة | من أين تجيبه |
|---|---|---|---|
| ١ | `GEMINI_API_KEYS` | مفاتيح مفصولة بفواصل | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| ٢ | `TELEGRAM_BOT_TOKEN` | `123456789:AA…` | @BotFather → `/newbot` |
| ٣ | `TELEGRAM_ADMIN_CHAT_ID` | رقمك (مثل `123456789`) | @userinfobot |

### بديل للمفاتيح المرقّمة

بدل `GEMINI_API_KEYS` يمكنك إضافة خمسة أسرار منفصلة — **النظام يقرأ الاثنين**:

```
GEMINI_API_KEY_1 … GEMINI_API_KEY_5
```

> 💡 **٥ مفاتيح أفضل من واحد**: النظام يبدّل تلقائياً عند تعطّل أي مفتاح أو
> انتهاء حصّته، فالنشر لا يتوقّف.

---

## ٢) مزامنة المستودع الخاص — سرّان (موصى بهما بشدّة)

| الاسم | القيمة | مثال |
|---|---|---|
| `STATE_REPO` | `owner/autonews-state` | `sajjad/autonews-state` |
| `STATE_REPO_TOKEN` | PAT يقرأ ويكتب ذلك المستودع | `ghp_…` |

### كيف تصنع التوكن (أفضل طريقة = fine-grained)

```
github.com/settings/tokens?type=beta  →  Generate new token
  Token name      : autonews-state-writer
  Expiration      : 90 days   (أو No expiration — أقل أماناً)
  Repository access: Only select repositories → autonews-state ✅
  Permissions:
    ☑ Contents        : Read and write      ← الوحيد المطلوب
    ☑ Metadata        : Read-only (تلقائي)
  → Generate → انسخه
```

### أو PAT كلاسيكي (أسرع، أقل أماناً)

```
github.com/settings/tokens  →  Generate new token (classic)
  Scopes: ☑ repo            ← يمنح كل مستودعاتك
```

> ⚠️ **الفرق مهمّ**: الـ fine-grained يصل لمستودع واحد فقط. الكلاسيكي يصل
> لكل مستودعاتك. لو تسرّب الكلاسيكي، الخطر أكبر بكثير.

### 🚀 أمر واحد ينشئ المستودع ويربطه

```bash
export STATE_REPO_TOKEN="ghp_..."      # أو github_pat_...
bash scripts/bootstrap_state_repo.sh
```

يُنشئ `autonews-state` (خاص) + يدفع الحالة الحالية + يطبع لك اسمَي السرّين.

---

## ٣) اختياري — حسب ما تفعّله

| الاسم | متى تحتاجه | القيمة |
|---|---|---|
| `SETUP_ALLOWED_USERS` | **يُنصح به دائماً** | رقمك، لمنع غيرك من التحكّم بالبوت |
| `TELEGRAM_CHAT_ID` | القناة الرئيسية عبر ملف الإعداد | `-100…` أو `@name` |
| `TELEGRAM_ANIME_CHAT_ID` | قناة الأنمي | |
| `TELEGRAM_SPORTS_CHAT_ID` | قناة الرياضة | |
| `TELEGRAM_TECH_CHAT_ID` | قناة التقنية | |
| `TELEGRAM_ECON_CHAT_ID` | قناة الاقتصاد | |
| `TELEGRAM_GAMES_CHAT_ID` | قناة الألعاب | |
| `TELEGRAM_LOCAL_CHAT_ID` | قناة المحافظات | |
| `FACEBOOK_PAGE_TOKEN` | إن أردت النشر على فيسبوك | توكن دائم |
| `FACEBOOK_PAGE_ID` | اختياري (يُحفظ من البوت) | `1328…` |
| `ALERT_BOT_TOKEN` + `ALERT_CHAT_ID` | تنبيهات لمشرف آخر | |
| `X_API_KEY` … | **مُعطّل** — لا تحتاجه | |

> 💡 **معرّفات القنوات لا تحتاج أسراراً أصلاً**: البوت يحفظها في
> `state/channels_setup.json` عند ربطها من اللوحة، فتُزامَن مع المستودع الخاص.

---

## ٤) المتغيّرات (Variables) لا الأسرار

هذه **ليست سرّاً** — ضعها في `Settings → Secrets and variables → Actions → Variables`:

| الاسم | القيمة | الأثر |
|---|---|---|
| `REQUIRE_REVIEW` | `false` | `true` = كل منشور ينتظر موافقتك (يؤخّر النشر!) |
| `MAX_ITEMS_PER_RUN` | `12` | سقف المعالجة |
| `DRY_RUN` | `false` | `true` = معاينة بلا نشر |

---

## ٥) كيف تتحقّق أن كل شيء مضبوط؟

### من GitHub

```
Actions → 🧪 CI → Run workflow
```
يجب أن يظهر ✅. ثم:

```
Actions → 🎛 لوحة التحكّم والبوت → Run workflow
```
قبل النشر يطبع حالة المزامنة:

```
🔗 حالة المزامنة مع المستودع الخاص
──────────────────────────────────────────────────
✅ مفعّلة → https://github.com/you/autonews-state.git (فرع main)
   ● state/ — 3 ملفاً · 16.0 ك.ب
   ● config/ — 3 ملفاً · 52.9 ك.ب
```

إن ظهر `⚪ غير مضبوطة` ← السرّان ناقصان أو اسم المستودع خطأ.

### من طرفية (اختياري)

```bash
python -m autonews.preflight --online
```

---

## ٦) 🔐 قواعد الأمان — اقرأها مرّة

| القاعدة | السبب |
|---|---|
| **لا تكتب سرّاً في محادثة أبداً** | المحادثة سجلّ دائم — السرّ يُعدّ محروقاً |
| **لا تضع سرّاً في ملف ثم ترفعه** | حتى لو حذفته، يبقى في تاريخ Git |
| **افحص بعد أي إضافة**: `bash scripts/check_secrets.sh --history` | يكشف التسريب |
| **أبطل أي سرّ انكشف** (Reset/Revoke) | الحذف وحده لا يكفي |
| **فعّل Push protection** | Settings → Code security |
| **أقلّ صلاحية ممكنة** | fine-grained بدل classic |

### ما يفعله المشروع تلقائياً

| الحماية | أين |
|---|---|
| فحص ٨ أنماط أسرار في كل دفع | `ci.yml` → `check_secrets.sh` |
| فحص تاريخ Git (٥٠ commit) | `ci.yml` → `--history` |
| حجب الأسرار من سجلات المزامنة | `state_sync._scrub` |
| رفض الحالة/السجلات في المستودع العام | `ci.yml` → فحص البنية |
| لا توكن في `.git/config` | `state_sync` يستخدم `x-access-token` في الذاكرة |

---

## ٧) أخطاء شائعة

| العَرَض | السبب | الحلّ |
|---|---|---|
| `ℹ️ المزامنة غير مضبوطة` | اسم السرّ خطأ | `STATE_REPO` بالحرف الكبير |
| `HTTP 401` عند الدفع | التوكن منتهٍ/بلا صلاحية | جدّد الـ PAT |
| `404` عند الدفع | المستودع غير موجود أو الاسم خطأ | `owner/autonews-state` بالضبط |
| البوت لا يرى المفاتيح | مسافة في القيمة | أعد اللصق بلا مسافات |
| `GEMINI` يعمل مرة ويفشل | مفتاح واحد فقط | أضف ٥ مفاتيح |
| كل تشغيل ينشر نفس الخبر | `STATE_REPO` غير مضبوط | فعّل المزامنة |

---

## ✅ قائمة تحقّق

- [ ] `GEMINI_API_KEYS` (أو ٥ مرقّمة)
- [ ] `TELEGRAM_BOT_TOKEN`
- [ ] `TELEGRAM_ADMIN_CHAT_ID`
- [ ] `STATE_REPO`
- [ ] `STATE_REPO_TOKEN` (fine-grained، Contents: RW)
- [ ] `SETUP_ALLOWED_USERS`
- [ ] Push protection مفعّل
- [ ] `bash scripts/check_secrets.sh --history` نظيف
