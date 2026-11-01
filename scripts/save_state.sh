#!/usr/bin/env bash
# =============================================================================
#  حفظ الحالة والتقارير داخل المستودع (Git) — بديل موثوق عن الـ Cache.
#
#  لماذا؟
#    cache في GitHub Actions:
#      • يُحذف بعد 7 أيام بلا استخدام  ⇒ يفقد النظام ذاكرته ويُنشر مكرراً
#      • مفتاحه مرتبط بـ run_id        ⇒ مساران مختلفان لا يريان حالة بعضهما
#    الـ commit:
#      • دائم، مشترك بين كل السير عمل، ومنظور لك في المستودع
#
#  الاستخدام:  bash scripts/save_state.sh "[skip ci] تحديث الحالة"
# =============================================================================
set -u

MSG="${1:-[skip ci] تحديث الحالة والتقارير}"
PATHS=(state logs config)   # config: البصمة تُكتب في channels.yml من البوت

# لا نفشل التشغيل أبداً بسبب الحفظ: النشر أهم من الحالة
fail() { echo "⚠️ تعذّر حفظ الحالة: $*" >&2; exit 0; }

git config user.name  "autonews-bot"            || fail "git config name"
git config user.email "autonews-bot@users.noreply.github.com" || fail "git config email"

# احذف ملفات القفل التي قد تعطّل الـ rebase عند تشغيلين متزامنين
cleanup_locks() { rm -f .git/index.lock .git/rebase-merge/.git-rebase-todo.lock 2>/dev/null || true; }

# أضف ما هو موجود فقط (المجلد الغائب لا يُفشل الحفظ)
mkdir -p state logs 2>/dev/null || true
EXISTING=()
for d in "${PATHS[@]}"; do [ -d "$d" ] && EXISTING+=("$d"); done
[ ${#EXISTING[@]} -eq 0 ] && { echo "ℹ️ لا مجلدات حالة — لا شيء يُحفظ"; exit 0; }
git add -A "${EXISTING[@]}" 2>/dev/null || fail "git add"

if git diff --cached --quiet; then
  echo "ℹ️ لا تغييرات في الحالة — لا حاجة لـ commit"
  exit 0
fi

git commit -m "$MSG" --no-verify >/dev/null 2>&1 || fail "git commit"
echo "✅ تمّ commit للحالة"

# لا remote؟ (بيئة اختبار محلية) — نتوقّف هنا بلا انتظار
if ! git remote get-url origin >/dev/null 2>&1; then
  echo "ℹ️ لا يوجد remote — commit محلي فقط"
  exit 0
fi

# دفع مع إعادة محاولة (قد يكون التشغيل الآخر دفع قبلك)
for i in 1 2 3; do
  if git push --quiet 2>/dev/null; then
    echo "✅ تمّ دفع الحالة إلى المستودع"
    exit 0
  fi
  echo "↻ محاولة الدمج وإعادة الدفع ($i/3)"
  cleanup_locks
  git pull --rebase --autostash --quiet 2>/dev/null || { cleanup_locks; git rebase --abort 2>/dev/null; }
  sleep $((i * 2))
done

echo "⚠️ تعذّر دفع الحالة بعد 3 محاولات (لن يُفشل التشغيل)" >&2
exit 0
