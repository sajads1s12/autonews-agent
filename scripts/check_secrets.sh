#!/usr/bin/env bash
# =============================================================================
#  🔐 فحص تسرّب الأسرار — الملفات الحالية + تاريخ Git
#
#  الاستخدام:
#     bash scripts/check_secrets.sh              # الملفات الحالية فقط (سريع)
#     bash scripts/check_secrets.sh --history    # + تاريخ Git كاملاً (بطيء)
#     bash scripts/check_secrets.sh --strict     # فشل حتى على التحذيرات
#
#  يعمل في CI عند كل دفع — يمنع رفع سرّ من الأساس.
# =============================================================================
set -uo pipefail

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
FOUND=0
CHECK_HISTORY=0
STRICT=0
for arg in "$@"; do
  case "$arg" in
    --history) CHECK_HISTORY=1 ;;
    --strict)  STRICT=1 ;;
  esac
done

# أنماط الأسرار الحقيقية (طويلة ومحدّدة لتقليل الإنذارات الكاذبة)
PATTERNS=(
  'EAA[A-Za-z0-9]{50,}'                       # توكن فيسبوك
  '[0-9]{8,12}:AA[A-Za-z0-9_-]{30,}'          # توكن بوت تيليجرام
  'AIza[0-9A-Za-z_-]{35}'                     # مفتاح Google/Gemini
  'AQ\.Ab8R[A-Za-z0-9_-]{20,}'                # مفتاح Gemini الجديد (AQ.Ab8R…)
  '[0-9a-f]{32}'                              # App Secret فيسبوك (٣٢ hex)
  'gh[pousr]_[A-Za-z0-9]{36}'                 # توكن GitHub
  'github_pat_[A-Za-z0-9_]{50,}'              # GitHub fine-grained PAT
  'xox[baprs]-[A-Za-z0-9-]{10,}'              # Slack
)

# استثناءات: ملفات ليست كوداً قابلاً للفحص + قيم وهمية شائعة في الاختبارات
EXCLUDES=(
  --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.pytest_cache
  --exclude-dir=node_modules --exclude-dir=.state-repo --exclude-dir=.state-archive
  --exclude='*.md' --exclude='*.example' --exclude='check_secrets.sh'
  --exclude='.env' --exclude='.env.*'
  --exclude='restore.sh' --exclude='*.lock'
)

# قيم وهمية تُستخدم في الاختبارات — لا تُعدّ تسرّباً
FALSE_POSITIVE='example|dummy|xxxx|your_|fake|FAKE|test_|sample|<.*>|\.\.\.'

# =============================================================================
echo "🔐 فحص تسرّب الأسرار — الملفات الحالية"
# =============================================================================
for pattern in "${PATTERNS[@]}"; do
  # لا نطبع محتوى السطر؛ قد يحتوي على السر نفسه. نعرض الملف ورقم السطر فقط.
  hits=$(grep -rInE "$pattern" "${EXCLUDES[@]}" . 2>/dev/null \
         | grep -viE "$FALSE_POSITIVE" \
         | sed -E 's/^([^:]+:[0-9]+):.*/\1/' | sort -u || true)
  if [ -n "$hits" ]; then
    echo "${RED}❌ نمط سرّ وُجد:${RESET} $pattern"
    echo "$hits" | head -5 | sed 's/^/   /'
    FOUND=1
  fi
done

# الملفات الخاصة لا تُعدّ تسرّباً لمجرد وجود نسخة محلية مستثناة من Git.
# الخطر الحقيقي: أن تكون متتبّعة (tracked) أو غير مستثناة وقد تدخل في commit.
GIT_REPO=0
if git rev-parse --git-dir >/dev/null 2>&1; then GIT_REPO=1; fi
for f in .env .env.local secrets.json credentials.json service-account.json; do
  if [ -f "$f" ]; then
    if [ "$GIT_REPO" -eq 1 ] && git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
      echo "${RED}❌ ملف سرّ متتبّع في Git:${RESET} $f"
      FOUND=1
    elif [ "$GIT_REPO" -eq 1 ] && git check-ignore -q -- "$f"; then
      echo "ℹ️ ملف محلي مستثنى من Git: $f"
    else
      echo "${RED}❌ ملف خاص غير مستثنى من Git:${RESET} $f"
      FOUND=1
    fi
  fi
done

# ملفات مضغوطة/نسخ احتياطية (قد تحوي أسراراً أو حالة خاصة)
ARCHIVES=$(find . -maxdepth 3 \( -name '*.tar.gz' -o -name '*.tgz' -o -name '*.zip' \
           -o -name '*.7z' -o -name '*.rar' -o -name '*.bak' \) \
           -not -path './.git/*' -not -path './.state-*' 2>/dev/null | head -5)
if [ -n "$ARCHIVES" ]; then
  echo "${YELLOW}⚠️ ملفات مضغوطة/احتياطية (راجعها قبل الرفع):${RESET}"
  echo "$ARCHIVES" | sed 's/^/   /'
  [ "$STRICT" -eq 1 ] && FOUND=1
fi

# =============================================================================
if [ "$CHECK_HISTORY" -eq 1 ]; then
  echo ""
  echo "🕵️ فحص تاريخ Git — هل دخل سرّ في commit سابق؟"
  if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "   ℹ️ ليس مستودع git — تخطّي"
  else
    HISTORY_HITS=0
    # نفحص الرسائل والمحتوى في كل الـ commits (سريع نسبياً بلا --all للملفات الثنائية)
    for pattern in 'EAA[A-Za-z0-9]{50,}' '[0-9]{8,12}:AA[A-Za-z0-9_-]{30,}' \
                   'AIza[0-9A-Za-z_-]{35}' 'AQ\.Ab8R[A-Za-z0-9_-]{20,}' \
                   'gh[pousr]_[A-Za-z0-9]{36}'; do
      out=$(git log --all --oneline -S"$(echo "$pattern" | head -c 4)" --pickaxe-regex \
            -E 2>/dev/null | head -3)
      # فحص أدقّ: grep على كل محتوى التاريخ (محدود لتفادي البطء)
      deep=$(git grep -nIE "$pattern" $(git rev-list --all 2>/dev/null | head -50) \
             2>/dev/null | grep -viE "$FALSE_POSITIVE" | head -3)
      if [ -n "$deep" ]; then
        echo "${RED}❌ سرّ في تاريخ Git:${RESET} $pattern"
        echo "$deep" | sed 's/^/   /'
        HISTORY_HITS=1
        FOUND=1
      fi
    done
    if [ "$HISTORY_HITS" -eq 0 ]; then
      echo "${GREEN}✅ لا أسرار في تاريخ Git (آخر ٥٠ commit)${RESET}"
    else
      echo ""
      echo "${YELLOW}🧹 التنظيف المطلوب:${RESET}"
      echo "   الطريقة الأسهل (تحذف التاريخ كله — مناسبة لمستودع جديد):"
      echo "     git checkout --orphan clean && git add -A && git commit -m 'نسخة نظيفة'"
      echo "     git branch -D main && git branch -m main && git push -f origin main"
      echo "   أو استخدم git-filter-repo لحذف الملف فقط:"
      echo "     git filter-repo --path secrets.json --invert-paths"
    fi
  fi
fi

# =============================================================================
if [ "$FOUND" -eq 0 ]; then
  echo "${GREEN}✅ لا أسرار متسرّبة${RESET}"
  exit 0
fi
echo ""
echo "${YELLOW}⚠️ أزل الأسرار ثم:${RESET}"
echo "   git rm --cached <file> && git commit -m 'إزالة سرّ'"
echo "   وفعّل GitHub Settings → Code security → Secret scanning + Push protection."
echo "   🚨 وأبطل السرّ من مصدره (Reset/Revoke) — الحذف وحده لا يكفي."
exit 1
