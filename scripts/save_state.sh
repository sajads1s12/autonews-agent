#!/usr/bin/env bash
# =============================================================================
#  💾 حفظ الحالة — متوافق مع الطريقتين (لا يكسر شيئاً)
#
#  الطريقة الحديثة (مفضّلة): المستودع الخاص عبر python -m autonews.state_sync
#  الطريقة القديمة:          commit داخل المستودع نفسه (للتوافق والإعدادات المحلية)
#
#  السلوك:
#    • إن كان STATE_REPO + STATE_REPO_TOKEN مضبوطين ⇒ يزامن مع الخاص
#    • وإلا ⇒ يحفظ داخل المستودع الحالي (وضع التطوير)
#    • في الحالتين: **لا يُفشل التشغيل أبداً**
#
#  الاستخدام:  bash scripts/save_state.sh "[state] تحديث الحالة"
# =============================================================================
set -u

MSG="${1:-[state] تحديث الحالة والتقارير}"
PATHS=(state logs config)

fail() { echo "⚠️ تعذّر حفظ الحالة: $*" >&2; exit 0; }

# ---------------------------------------------------------------------------
#  ١) الطريقة الحديثة: المستودع الخاص
# ---------------------------------------------------------------------------
if [ -n "${STATE_REPO:-}" ] && [ -n "${STATE_REPO_TOKEN:-}" ] \
   && [ -f src/autonews/state_sync.py ]; then
  echo "🔗 الحفظ في المستودع الخاص ($STATE_REPO)…"
  PYTHONPATH="${PYTHONPATH:-src}" python -m autonews.state_sync save --message "$MSG" \
    && exit 0
  echo "↩️ تعذّرت المزامنة — نجرّب الحفظ المحلي كخطة بديلة" >&2
fi

# ---------------------------------------------------------------------------
#  ٢) الطريقة القديمة: commit محلي (وضع التطوير / مستودع واحد)
# ---------------------------------------------------------------------------
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "ℹ️ ليس مستودع git — لا شيء يُحفظ"
  exit 0
fi

git config user.name  "autonews-bot"                                     || fail "git config name"
git config user.email "autonews-bot@users.noreply.github.com"            || fail "git config email"
cleanup_locks() { rm -f .git/index.lock .git/rebase-merge/.git-rebase-todo.lock 2>/dev/null || true; }

mkdir -p state logs 2>/dev/null || true
EXISTING=()
for d in "${PATHS[@]}"; do [ -d "$d" ] && EXISTING+=("$d"); done
[ ${#EXISTING[@]} -eq 0 ] && { echo "ℹ️ لا مجلدات حالة"; exit 0; }

# نتجاهل الملفات المؤقّتة وأرشيف المزامنة
git add -A "${EXISTING[@]}" 2>/dev/null || fail "git add"

if git diff --cached --quiet; then
  echo "ℹ️ لا تغييرات في الحالة"
  exit 0
fi

git commit -m "$MSG" --no-verify >/dev/null 2>&1 || fail "git commit"
echo "✅ commit للحالة (محلي)"

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "ℹ️ لا يوجد remote — commit محلي فقط"
  exit 0
fi

for i in 1 2 3; do
  if git push --quiet 2>/dev/null; then
    echo "✅ دُفعت الحالة"
    exit 0
  fi
  echo "↻ محاولة دمج وإعادة دفع ($i/3)"
  cleanup_locks
  git pull --rebase --autostash --quiet 2>/dev/null || { cleanup_locks; git rebase --abort 2>/dev/null; }
  sleep $((i * 2))
done

echo "⚠️ تعذّر الدفع بعد 3 محاولات (لن يُفشل التشغيل)" >&2
exit 0
