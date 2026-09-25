#!/usr/bin/env bash
# Add pretix_event_analytics 2.0.0 to /opt/docker/suti-pretix (pretix 2026.7.0).
# Builds from a COPY of ~/suti-upgrade/build (ticketswap + theme lines kept) plus one analytics line.
# Auto-rollback to the previous image if any check fails. No sudo needed.
set -uo pipefail
cd /opt/docker/suti-pretix
A=~/suti-analytics; U=~/suti-upgrade; STAMP=$(date +%Y%m%d-%H%M%S)
BUILD=$A/build; DUMP=$A/backup/pretix-pre-analytics-$STAMP.dump; FP=$A/backup/fingerprint-pre-analytics-$STAMP.txt
WHEEL=pretix_event_analytics-2.0.0-py3-none-any.whl
NEW=suti-pretix-pretix:2026.7.0-analytics; RB=suti-pretix-pretix:rollback-pre-analytics
mkdir -p $A/backup
fail() { echo "ABORT: $*"; exit 1; }
status() { docker compose exec -T pretix python3 -c "
import sys, urllib.request, urllib.error
class N(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None
r = urllib.request.Request('http://localhost' + sys.argv[1], headers={'Host': 'tickets.sutifestival.com', 'X-Forwarded-Proto': 'https'})
try: print(urllib.request.build_opener(N).open(r, timeout=20).status)
except urllib.error.HTTPError as e: print(e.code)
except Exception: print(0)" "$1" 2>/dev/null | tr -d '\r'; }
ready() { for i in $(seq 1 60); do [ "$(status /control/login)" = 200 ] && return 0; sleep 5; done; return 1; }
fp() { docker compose exec -T db psql -U postgres -d pretix -At -F ' ' < $U/fingerprint.sql; }
rollback() {
  echo "!!! ROLLING BACK: $*"
  docker tag $RB suti-pretix-pretix:latest
  docker compose up -d --no-build pretix
  ready && echo "previous image back up" || echo "previous image NOT answering"
  echo "(plugin tables, if created, are unused by the old image and harmless; DB dump: $DUMP)"
  exit 2
}

echo "== $(date -u +%T) build $NEW while the current version keeps serving"
rm -rf $BUILD && mkdir -p $BUILD && cp $U/build/* $BUILD/ && cp $A/$WHEEL $BUILD/ || fail "build context"
grep -q "pretix_suti_theme" $BUILD/Dockerfile && grep -q "pretix_ticketswap" $BUILD/Dockerfile || fail "base Dockerfile lost theme/ticketswap lines"
sed -i "s|^USER pretixuser|COPY $WHEEL /tmp/\nRUN pip3 install /tmp/$WHEEL\nUSER pretixuser|" $BUILD/Dockerfile
cat $BUILD/Dockerfile
docker build -t $NEW $BUILD > $A/build-$STAMP.log 2>&1 || fail "docker build failed, see $A/build-$STAMP.log"
PKGS=$(docker run --rm --entrypoint pip3 $NEW list 2>/dev/null | grep -i -E "^pretix(-| )" )
echo "$PKGS"
for p in "pretix .*2026.7.0" "pretix-event-analytics .*2.0.0" "pretix-suti-theme" "pretix-ticketswap .*1.0.4"; do
  echo "$PKGS" | grep -qiE "^$p" || fail "image is missing $p"
done

echo "== $(date -u +%T) pre-flight: migration plan of the NEW image against the live DB (read-only)"
NET=$(docker inspect suti-pretix-db-1 --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
docker run --rm --network "$NET" -v /opt/docker/suti-pretix/pretixconf:/etc/pretix:ro --tmpfs /data:rw,uid=15371,gid=15371 --entrypoint python3 $NEW   -m pretix migrate --plan > $A/migrate-plan-$STAMP.log 2>&1 || { tail -5 $A/migrate-plan-$STAMP.log; fail "migration plan failed (nothing changed)"; }
grep -E "^Planned|pretix_event_analytics|^  (Raw|Create|Add|Alter|Remove|Rename)" $A/migrate-plan-$STAMP.log | head -20
PLANNED=$(grep -c "^pretix_event_analytics\." $A/migrate-plan-$STAMP.log)
OTHER=$(grep -E "^[a-z_]+\.[0-9]{4}" $A/migrate-plan-$STAMP.log | grep -vc "^pretix_event_analytics\.")
echo "   planned: $PLANNED analytics migrations, $OTHER other"
[ "$OTHER" = 0 ] || fail "unexpected non-analytics migrations planned (nothing changed)"

echo "== $(date -u +%T) salt for hashed identities (generated here, never printed)"
docker compose exec -T -u pretixuser pretix sh -c 'test -e /etc/pretix/pretix.cfg.bak-pre-analytics || cp /etc/pretix/pretix.cfg /etc/pretix/pretix.cfg.bak-pre-analytics'
if docker compose exec -T pretix grep -q "^\[pretix_event_analytics\]" /etc/pretix/pretix.cfg; then
  echo "   salt already configured, unchanged"
else
  docker compose exec -T -u pretixuser pretix python3 -c "
import secrets
with open('/etc/pretix/pretix.cfg', 'a') as f:
    f.write('\n[pretix_event_analytics]\n; HMAC salt for analytics identity hashes. NEVER change it once data exists.\nsecret_salt=' + secrets.token_hex(32) + '\n')
" || fail "could not write salt"
  echo "   salt added"
fi

echo "== $(date -u +%T) stop pretix, dump + fingerprint"
docker tag suti-pretix-pretix:latest $RB
docker compose stop pretix || fail "could not stop pretix"
docker compose exec -T db pg_dump -U postgres -d pretix -Fc > "$DUMP" || { docker compose start pretix; fail "dump failed, pretix restarted"; }
T=$(docker compose exec -T db pg_restore --list < "$DUMP" | grep -c "TABLE DATA"); echo "   dump: $(du -h "$DUMP" | cut -f1), $T tables"
[ "$T" -gt 100 ] || { docker compose start pretix; fail "dump looks incomplete, pretix restarted"; }
fp > "$FP"

echo "== $(date -u +%T) switch image and start (migrations create the plugin tables)"
docker tag $NEW suti-pretix-pretix:latest
docker compose up -d --no-build pretix || rollback "container did not start"
ready || rollback "pretix not answering"
N=$(docker compose exec -T db psql -U postgres -d pretix -Atc "select count(*) from django_migrations where app='pretix_event_analytics'" | tr -d '\r')
echo "   analytics migrations applied: $N"
[ "$N" = 8 ] || rollback "expected 8 analytics migrations, found $N"
fp | diff "$FP" - || rollback "ticket data changed"
echo "   ticket data identical"
for p in /control/login /suti/ /suti/2026/; do s=$(status "$p"); echo "   $p -> $s"; [ "$s" = 200 ] || rollback "$p returned $s"; done
echo "== $(date -u +%T) DEPLOY OK   dump=$DUMP   rollback image=$RB"
