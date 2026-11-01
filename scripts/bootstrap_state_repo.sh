#!/usr/bin/env bash
# =============================================================================
#  🏗 إنشاء مستودع الحالة الخاص وربطه — بأمر واحد
#
#  ما يفعله:
#    ١) ينشئ مستودع `autonews-state` على GitHub (خاص) عبر الـ API
#    ٢) يدفع الحالة الحالية إليه (state/ · logs/ · config/)
#    ٣) يطبع لك بالضبط أسماء الأسرار التي تضيفها في المستودع العام
#
#  المتطلبات:
#    • STATE_REPO_TOKEN  : PAT بصلاحية repo (classic) أو Contents: Read/Write
#    • GITHUB_OWNER      : اسم حسابك (اختياري — يُستنتج من التوكن)
#
#  ليس حصرياً: يمكنك إنشاء المستودع يدوياً من الواجهة ثم تشغيل:
#    bash scripts/bootstrap_state_repo.sh --push-only
# =============================================================================
set -uo pipefail

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
ok()   { echo "${GREEN}✅ $*${RESET}"; }
warn() { echo "${YELLOW}⚠️  $*${RESET}"; }
die()  { echo "${RED}❌ $*${RESET}" >&2; exit 1; }

REPO_NAME="${STATE_REPO_NAME:-autonews-state}"
PUSH_ONLY=0
[ "${1:-}" = "--push-only" ] && PUSH_ONLY=1

[ -z "${STATE_REPO_TOKEN:-}" ] && die "STATE_REPO_TOKEN غير مضبوط.
   أنشئ PAT من: github.com/settings/tokens  (صلاحية repo)
   ثم:  export STATE_REPO_TOKEN=ghp_xxx"

API="https://api.github.com"
AUTH="Authorization: token ${STATE_REPO_TOKEN}"

# ---------------------------------------------------------------------------
#  ١) من أنا؟ (نستنتج المالك من التوكن — لا نحتاج سؤالك)
# ---------------------------------------------------------------------------
echo "🔍 أتحقّق من الحساب…"
USER_JSON=$(curl -sS -H "$AUTH" -H "Accept: application/vnd.github+json" "$API/user" 2>/dev/null)
OWNER=$(echo "$USER_JSON" | python3 -c "import json,sys;print(json.load(sys.stdin).get('login',''))" 2>/dev/null)
[ -z "$OWNER" ] && die "التوكن غير صالح. تحقّق منه: curl -H 'Authorization: token $STATE_REPO_TOKEN' $API/user"
ok "الحساب: $OWNER"

if [ -n "${GITHUB_OWNER:-}" ] && [ "$GITHUB_OWNER" != "$OWNER" ]; then
  OWNER="$GITHUB_OWNER"
  warn "استخدمت GITHUB_OWNER=$OWNER بدل حساب التوكن"
fi

FULL="${OWNER}/${REPO_NAME}"

# ---------------------------------------------------------------------------
#  ٢) إنشاء المستودع إن لم يكن موجوداً
# ---------------------------------------------------------------------------
if [ "$PUSH_ONLY" -eq 1 ]; then
  echo "ℹ️ --push-only: سأتجاوز إنشاء المستودع"
else
  echo "🧱 أتحقّق من وجود $FULL…"
  STATUS=$(curl -sS -o /dev/null -w "%{http_code}" -H "$AUTH" "$API/repos/$FULL")
  if [ "$STATUS" = "200" ]; then
    ok "المستودع موجود مسبقاً"
  elif [ "$STATUS" = "404" ]; then
    echo "📦 أنشئ المستودع الخاص $FULL…"
    CREATE=$(curl -sS -X POST -H "$AUTH" -H "Accept: application/vnd.github+json" \
      "$API/user/repos" \
      -d "{\"name\":\"$REPO_NAME\",\"private\":true,\"description\":\"AutoNews — ذاكرة البوت (خاص)\",\"auto_init\":false}" 2>/dev/null)
    echo "$CREATE" | grep -q '"full_name"' && ok "أُنشئ $FULL (خاص)" \
      || die "تعذّر الإنشاء: $(echo "$CREATE" | head -c 200)"
  else
    die "ردّ غير متوقّع من GitHub: HTTP $STATUS (هل انتهت صلاحية التوكن؟)"
  fi
fi

# ---------------------------------------------------------------------------
#  ٣) دفع الحالة الحالية
# ---------------------------------------------------------------------------
export STATE_REPO="$FULL"
export STATE_REPO_URL="https://github.com/$FULL.git"

echo "📤 أدفع الحالة الحالية…"
if [ -f src/autonews/state_sync.py ]; then
  PYTHONPATH="${PYTHONPATH:-src}" python3 -m autonews.state_sync save \
    --message "[state] التهيئة الأولى" || warn "تعذّر الدفع — راجع الرسالة أعلاه"
else
  warn "state_sync.py غير موجود — تخطّي"
fi

# ---------------------------------------------------------------------------
#  ٤) التعليمات النهائية
# ---------------------------------------------------------------------------
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "✅ المستودع الخاص جاهز: https://github.com/$FULL"
echo ""
echo "📋 أضف هذين السرّين في المستودع **العام** (autonews-agent):"
echo "   Settings → Secrets and variables → Actions → New repository secret"
echo ""
echo "   STATE_REPO        = $FULL"
echo "   STATE_REPO_TOKEN  = (نفس التوكن الذي استخدمته)"
echo ""
echo "🔐 ثم قلّل صلاحية التوكن (اختياري لكن مستحسن):"
echo "   fine-grained → Repository access: $REPO_NAME فقط"
echo "               → Permissions: Contents = Read and write"
echo ""
echo "🧪 للتحقّق:"
echo "   STATE_REPO=$FULL STATE_REPO_TOKEN=*** python3 -m autonews.state_sync status"
echo "═══════════════════════════════════════════════════════════════"
