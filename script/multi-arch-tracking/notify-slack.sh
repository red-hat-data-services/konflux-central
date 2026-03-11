#!/usr/bin/env bash
#
# Send a Smartsheet public URL to a Slack channel via incoming webhook.
#
# Required environment variables:
#   SLACK_WEBHOOK_URL        - Slack incoming webhook URL
#   SMARTSHEET_PUBLIC_URL    - Published Smartsheet URL to share
#
# Optional environment variables:
#   SLACK_MESSAGE_HEADER     - Custom header text (default: "Multi-Arch Support Matrix")
#
# Usage:
#   export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/T.../B.../xxx"
#   export SMARTSHEET_PUBLIC_URL="https://publish.smartsheet.com/..."
#   ./script/multi-arch-tracking/notify-slack.sh
#
#   # Or pipe directly from export-to-smartsheet.py:
#   eval "$(./script/multi-arch-tracking/export-to-smartsheet.py rhoai-3.4-ea.1)"
#   ./script/multi-arch-tracking/notify-slack.sh

set -euo pipefail

if [[ -z "${SLACK_WEBHOOK_URL:-}" ]]; then
    echo "Error: SLACK_WEBHOOK_URL is not set." >&2
    exit 1
fi

if [[ -z "${SMARTSHEET_PUBLIC_URL:-}" ]]; then
    echo "Error: SMARTSHEET_PUBLIC_URL is not set." >&2
    exit 1
fi

header="${SLACK_MESSAGE_HEADER:-Multi-Arch Support in $RHOAI_BRANCH}"

payload=$(cat <<EOF
{
  "blocks": [
    {
      "type": "header",
      "text": {
        "type": "plain_text",
        "text": "${header}"
      }
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "A new multi-arch support spreadsheet has been published for $RHOAI_BRANCH:\n\n<${SMARTSHEET_PUBLIC_URL}|View Smartsheet>. Data is based on Konflux configuration in <https://github.com/red-hat-data-services/konflux-central/|konflux-central> repository. $SLACK_MESSAGE_EXTRA_TEXT"
      }
    }
  ]
}
EOF
)

echo "Sending Slack notification..." >&2
http_code=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -d "${payload}" \
    "${SLACK_WEBHOOK_URL}")

if [[ "${http_code}" == "200" ]]; then
    echo "Slack notification sent successfully." >&2
else
    echo "Error: Slack webhook returned HTTP ${http_code}." >&2
    exit 1
fi
