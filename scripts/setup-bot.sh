#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

bot_config_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/lmk-wecom-gateway"
bot_config_file="${bot_config_dir}/bot.env"

if [[ -f "${bot_config_file}" ]]; then
  echo "${bot_config_file} already exists; refusing to overwrite it." >&2
  echo "Delete or rename it yourself only if you intend to replace the credentials." >&2
  exit 1
fi

read -r -p "Enterprise WeChat BotID: " bot_id
read -r -s -p "Enterprise WeChat Secret (input hidden): " bot_secret
printf '\n'

if [[ -z "${bot_id}" || -z "${bot_secret}" ]]; then
  echo "BotID and Secret must not be empty." >&2
  exit 1
fi

umask 077
mkdir -p "${bot_config_dir}"
chmod 700 "${bot_config_dir}"
{
  printf 'WECOM_BOT_ID=%q\n' "${bot_id}"
  printf 'WECOM_BOT_SECRET=%q\n' "${bot_secret}"
  printf 'WECOM_ALLOWED_USERS=%q\n' '*'
} > "${bot_config_file}"
chmod 600 "${bot_config_file}"

echo "Created ${bot_config_file} with permissions 600."
echo "The initial '*' allowlist is acceptable only while the bot visibility is limited to you."
