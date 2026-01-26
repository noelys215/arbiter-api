#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
AI_ON="${AI_ON:-0}"

COOKIE_A="/tmp/wp_user_a.cookies"
COOKIE_B="/tmp/wp_user_b.cookies"

cleanup() {
  rm -f "$COOKIE_A" "$COOKIE_B"
}
trap cleanup EXIT

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

curl_json() {
  local method="$1"
  local url="$2"
  local cookie_jar="${3:-}"
  local data="${4:-}"
  local resp status body

  local args=(
    -sS
    -X "$method"
    "$url"
  )

  if [[ -n "$cookie_jar" ]]; then
    args+=(-b "$cookie_jar" -c "$cookie_jar")
  fi

  if [[ -n "$data" ]]; then
    args+=(-H "Content-Type: application/json" -d "$data")
  fi

  resp=$(curl "${args[@]}" -w "\n__HTTP_STATUS__%{http_code}")
  status="${resp##*__HTTP_STATUS__}"
  body="${resp%$'\n'__HTTP_STATUS__*}"

  if [[ ! "$status" =~ ^2 ]]; then
    echo "Request failed: $method $url (status $status)" >&2
    echo "Response body:" >&2
    echo "$body" >&2
    exit 1
  fi

  if ! echo "$body" | jq -e . >/dev/null 2>&1; then
    echo "Non-JSON response from $method $url" >&2
    echo "$body" >&2
    exit 1
  fi

  echo "$body"
}

get_json_field() {
  local body="$1"
  local jq_expr="$2"
  local val
  val="$(echo "$body" | jq -r "$jq_expr" 2>/dev/null || true)"
  if [[ -z "$val" || "$val" == "null" ]]; then
    echo "Missing field: $jq_expr" >&2
    echo "$body" >&2
    exit 1
  fi
  echo "$val"
}

require_cmd jq
require_cmd curl

rm -f "$COOKIE_A" "$COOKIE_B"

echo "==> Health check"
curl_json GET "$BASE_URL/health"

suffix="$(date +%s)_$$"
a_email="a_e2e_${suffix}@x.com"
b_email="b_e2e_${suffix}@x.com"
a_user="a_${suffix}"
b_user="b_${suffix}"

echo "==> Register + login User A"
curl_json POST "$BASE_URL/auth/register" "$COOKIE_A" \
  "{\"email\":\"$a_email\",\"username\":\"$a_user\",\"display_name\":\"User A\",\"password\":\"SuperSecret123\"}"
curl_json POST "$BASE_URL/auth/login" "$COOKIE_A" \
  "{\"email\":\"$a_email\",\"password\":\"SuperSecret123\"}"

echo "==> Register + login User B"
curl_json POST "$BASE_URL/auth/register" "$COOKIE_B" \
  "{\"email\":\"$b_email\",\"username\":\"$b_user\",\"display_name\":\"User B\",\"password\":\"SuperSecret123\"}"
curl_json POST "$BASE_URL/auth/login" "$COOKIE_B" \
  "{\"email\":\"$b_email\",\"password\":\"SuperSecret123\"}"

echo "==> Fetch /me IDs"
me_a="$(curl_json GET "$BASE_URL/me" "$COOKIE_A")"
me_b="$(curl_json GET "$BASE_URL/me" "$COOKIE_B")"
USER_A_ID="$(get_json_field "$me_a" ".id")"
USER_B_ID="$(get_json_field "$me_b" ".id")"
echo "User A ID: $USER_A_ID"
echo "User B ID: $USER_B_ID"

echo "==> User A creates friend invite"
invite_body="$(curl_json POST "$BASE_URL/friends/invite" "$COOKIE_A")"
INVITE_CODE="$(get_json_field "$invite_body" ".code")"
echo "Invite code: $INVITE_CODE"

echo "==> User B accepts friend invite"
curl_json POST "$BASE_URL/friends/accept" "$COOKIE_B" \
  "{\"code\":\"$INVITE_CODE\"}" >/dev/null

echo "==> User A creates group"
group_body="$(curl_json POST "$BASE_URL/groups" "$COOKIE_A" \
  "{\"name\":\"Movie Night E2E\",\"member_user_ids\":[\"$USER_B_ID\"]}")"
GROUP_ID="$(get_json_field "$group_body" ".id")"
echo "Group ID: $GROUP_ID"

echo "==> User A adds watchlist items"
item1="$(curl_json POST "$BASE_URL/groups/$GROUP_ID/watchlist" "$COOKIE_A" \
  "{\"type\":\"tmdb\",\"tmdb_id\":603,\"media_type\":\"movie\",\"title\":\"The Matrix\",\"year\":1999,\"poster_path\":\"/x.jpg\"}")"
item2="$(curl_json POST "$BASE_URL/groups/$GROUP_ID/watchlist" "$COOKIE_A" \
  "{\"type\":\"tmdb\",\"tmdb_id\":604,\"media_type\":\"movie\",\"title\":\"The Matrix Reloaded\",\"year\":2003,\"poster_path\":\"/y.jpg\"}")"
item3="$(curl_json POST "$BASE_URL/groups/$GROUP_ID/watchlist" "$COOKIE_A" \
  "{\"type\":\"manual\",\"title\":\"The Thing\",\"year\":1982,\"media_type\":\"movie\"}")"

ITEM1_ID="$(get_json_field "$item1" ".id")"
ITEM2_ID="$(get_json_field "$item2" ".id")"
ITEM3_ID="$(get_json_field "$item3" ".id")"
echo "Watchlist item IDs: $ITEM1_ID $ITEM2_ID $ITEM3_ID"

echo "==> Create session"
if [[ "$AI_ON" == "1" ]]; then
  session_payload="$(jq -n --arg text "something chill, not too long" \
    '{constraints:{format:"any"},text:$text,duration_seconds:15,candidate_count:2}')"
else
  session_payload="$(jq -n '{constraints:{format:"any"},duration_seconds:15,candidate_count:2}')"
fi

session_body="$(curl_json POST "$BASE_URL/groups/$GROUP_ID/sessions" "$COOKIE_A" "$session_payload")"
SESSION_ID="$(get_json_field "$session_body" ".session_id")"
echo "Session ID: $SESSION_ID"
if [[ "$AI_ON" == "1" ]]; then
  echo "AI fields:"
  echo "$session_body" | jq '{ai_used, ai_why, parsed_by_ai: .constraints.parsed_by_ai, ai_version: .constraints.ai_version}'
fi

echo "==> User B fetches session state"
state_b="$(curl_json GET "$BASE_URL/sessions/$SESSION_ID" "$COOKIE_B")"
candidates_raw="$(echo "$state_b" | jq -r ".candidates[].watchlist_item_id")"
CANDIDATES=()
while IFS= read -r line; do
  [[ -n "$line" ]] && CANDIDATES+=("$line")
done <<<"$candidates_raw"

if [[ "${#CANDIDATES[@]}" -lt 2 ]]; then
  echo "Not enough candidates in deck:" >&2
  echo "$state_b" >&2
  exit 1
fi

C1="${CANDIDATES[0]}"
C2="${CANDIDATES[1]}"
echo "Candidate IDs: $C1 $C2"

echo "==> User A votes YES on $C1"
vote_a="$(curl_json POST "$BASE_URL/sessions/$SESSION_ID/vote" "$COOKIE_A" \
  "{\"watchlist_item_id\":\"$C1\",\"vote\":\"yes\"}")"
echo "$vote_a" | jq

echo "==> User B votes NO on $C2"
vote_b="$(curl_json POST "$BASE_URL/sessions/$SESSION_ID/vote" "$COOKIE_B" \
  "{\"watchlist_item_id\":\"$C2\",\"vote\":\"no\"}")"
echo "$vote_b" | jq

echo "==> Waiting for session expiry..."
sleep 16

echo "==> Polling for resolved session"
final_state=""
for _ in 1 2 3 4 5; do
  final_state="$(curl_json GET "$BASE_URL/sessions/$SESSION_ID" "$COOKIE_A")"
  status="$(echo "$final_state" | jq -r ".status")"
  if [[ "$status" == "complete" ]]; then
    break
  fi
  sleep 2
done

status="$(echo "$final_state" | jq -r ".status")"
if [[ "$status" != "complete" ]]; then
  echo "Session did not resolve in time:" >&2
  echo "$final_state" >&2
  exit 1
fi

WINNER_ID="$(get_json_field "$final_state" ".result_watchlist_item_id")"
echo "Winner watchlist_item_id: $WINNER_ID"
echo "Final session state:"
echo "$final_state" | jq

echo "==> Create second session"
session2_payload="$(jq -n '{constraints:{format:"any"},duration_seconds:15,candidate_count:2}')"
session2_body="$(curl_json POST "$BASE_URL/groups/$GROUP_ID/sessions" "$COOKIE_A" "$session2_payload")"
SESSION2_ID="$(get_json_field "$session2_body" ".session_id")"
echo "Session2 ID: $SESSION2_ID"

echo "==> User B calls shuffle"
shuffle_body="$(curl_json POST "$BASE_URL/sessions/$SESSION2_ID/shuffle" "$COOKIE_B")"
echo "$shuffle_body" | jq

echo "DONE: group=$GROUP_ID session=$SESSION_ID session2=$SESSION2_ID"
