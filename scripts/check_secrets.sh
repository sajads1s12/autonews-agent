#!/usr/bin/env bash
# =============================================================================
#  🔐 فحص تسرّب الأسرار — قبل كل push (ويُشغَّل في CI أيضاً)
#
#  يبحث عن أنماط الأسرار الفعلية (لا أسماء المتغيّرات) في كل ملفات المشروع.
#  الاستخدام:  bash scripts/check_secrets.sh
# =============================================================================
set -uo pipefail

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
FOUND=0

# أنماط الأسرار الحقيقية (طويلة ومحدّدة لتقليل الإنذارات الكاذبة)
PATTERNS=(
  'EAA[A-Za-z0-9]{50,}'                       # توكن فيسبوك
  '[0-9]{8,12}:[A-Za-z0-9_-]{30,}'            # توكن بوت تيليجرام
  'AIza[0-9A-Za-z_-]{35}'                     # مفتاح Google/Gemini
  '[0-9a-f]{32}'                              # App Secret فيسبوك (٣٢ hex)
  'ghp_[A-Za-z0-9]{36}'                       # توكن GitHub
  'xox[baprs]-[A-Za-z0-9-]{10,}'              # Slack
)

# ملاحظة: الأرشيف (restore.sh) و docs/أرشيف-المشروع.md ملفّات base64 —
# أي نمط سرّ يظهر فيها إنما هو صدفة ترميز، فنستثنيها من الفحص.
EXCLUDES=(--exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.pytest_cache
          --exclude-dir=node_modules --exclude='*.md' --exclude='*.example'
          --exclude='check_secrets.sh' --exclude='restore.sh')

echo "🔐 فحص تسرّب الأسرار…"
for pattern in "${PATTERNS[@]}"; do
  # نستثني القيم الوهمية الشائعة في الاختبارات (abcdef / 123456 / fake …)
  hits=$(grep -rInE "$pattern" "${EXCLUDES[@]}" . 2>/dev/null \
        | grep -viE "example|dummy|xxxx|your_|<.*>" || true)
  if [ -n "$hits" ]; then
    echo "${RED}❌ نمط سرّ وُجد:${RESET} $pattern"
    echo "$hits" | head -5 | sed 's/^/   /'
    FOUND=1
  fi
done

# ملفات لا يجب أن تكون موجودة أصلاً
for f in .env .env.local secrets.json; do
  if [ -f "$f" ]; then
    echo "${RED}❌ ملف سرّ يجب ألّا يُرفع:${RESET} $f"
    FOUND=1
  fi
done

if [ "$FOUND" -eq 0 ]; then
  echo "${GREEN}✅ لا أسرار متسرّبة${RESET}"
  exit 0
fi
echo ""
echo "${YELLOW}⚠️ أزل الأسرار من الملفات، ثم:${RESET}"
echo "   git rm --cached <file> && git commit -m 'إزالة سرّ'"
echo "   وفعّل GitHub Secret Scanning في Settings → Code security."
exit 1
