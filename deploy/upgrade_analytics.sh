#!/usr/bin/env bash
# Upgrade pretix_event_analytics in the suti0 image: swap ONLY the analytics wheel
# in a copy of ~/suti-upgrade/build (theme/ticketswap untouched). Only analytics
# migrations may be planned; on rollback they are reversed before the old image returns.
# Usage: upgrade_analytics.sh <old-version> <new-version> [<analytics migrations after upgrade, default 8>]
set -uo pipefail
OLD=${1:?old version}; NEWV=${2:?new version}; EXPECT=${3:-8}
cd /opt/docker/suti-pretix
A=~/suti-analytics; U=~/suti-upgrade; STAMP=$(date +%Y%m%d-%H%M%S)
BUILD=$A/build; DUMP=$A/backup/pretix-pre-analytics-$NEWV-$STAMP.dump; FP=$A/backup/fingerprint-pre-analytics-$NEWV-$STAMP.txt
OW=pretix_event_analytics-$OLD-py3-none-any.whl; NW=pretix_event_analytics-$NEWV-py3-none-any.whl
TAG=suti-pretix-pretix:2026.7.0-analytics$NEWV; RB=suti-pretix-pretix:rollback-pre-analytics$NEWV
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
NET=$(docker inspect suti-pretix-db-1 --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
newimg() { docker run --rm --network "$NET" -v /opt/docker/suti-pretix/pretixconf:/etc/pretix:ro --tmpfs /data:rw,uid=15371,gid=15371 --entrypoint python3 $TAG -m pretix "$@"; }
amig() { docker compose exec -T db psql -U postgres -d pretix -Atc "select $1 from django_migrations where app='pretix_event_analytics' $2" | tr -d '\r'; }
rollback() {
  echo "!!! ROLLING BACK: $*"
  if [ "$(amig 'count(*)' '')" != "$N0" ]; then
    echo "   reversing analytics migrations to $LAST0"
    docker compose stop pretix
    newimg migrate pretix_event_analytics "$LAST0" || echo "   REVERSE MIGRATION FAILED — restore $DUMP"
  fi
  docker tag $RB suti-pretix-pretix:latest
  docker compose up -d --no-build pretix
  ready && echo "previous image back up" || echo "previous image NOT answering"
  exit 2
}

echo "== $(date -u +%T) build $TAG from a copy of ~/suti-upgrade/build (only the analytics wheel changes)"
grep -q "$OW" $U/build/Dockerfile || fail "~/suti-upgrade/build does not contain analytics $OLD"
rm -rf $BUILD && mkdir -p $BUILD && cp $U/build/* $BUILD/ && rm $BUILD/$OW && cp $A/$NW $BUILD/ || fail "build context"
sed -i "s/$OW/$NW/g" $BUILD/Dockerfile
diff <(sed "s/$OW/$NW/g" $U/build/Dockerfile) $BUILD/Dockerfile >/dev/null || fail "Dockerfile differs beyond the wheel name"
cat $BUILD/Dockerfile
docker build -t $TAG $BUILD > $A/build-$NEWV-$STAMP.log 2>&1 || fail "docker build failed, see $A/build-$NEWV-$STAMP.log"
PKGS=$(docker run --rm --entrypoint pip3 $TAG list 2>/dev/null | grep -i -E "^pretix(-| )")
echo "$PKGS"
echo "$PKGS" | grep -qiE "^pretix-event-analytics +$NEWV" || fail "image lacks analytics $NEWV"
for p in "pretix .*2026.7.0" "pretix-suti-theme" "pretix-ticketswap"; do echo "$PKGS" | grep -qiE "^$p" || fail "image is missing $p"; done

echo "== $(date -u +%T) pre-flight: migration plan of the new image against the live DB (read-only)"
N0=$(amig 'count(*)' ''); LAST0=$(amig name 'order by name desc limit 1')
echo "   analytics migrations now: $N0 (last $LAST0), expected after: $EXPECT"
newimg migrate --plan > $A/migrate-plan-$NEWV-$STAMP.log 2>&1 || { tail -5 $A/migrate-plan-$NEWV-$STAMP.log; fail "migration plan failed (nothing changed)"; }
PLANNED=$(grep -cE "^[a-z_]+\.[0-9]{4}" $A/migrate-plan-$NEWV-$STAMP.log)
OTHER=$(grep -E "^[a-z_]+\.[0-9]{4}" $A/migrate-plan-$NEWV-$STAMP.log | grep -vc "^pretix_event_analytics\.")
echo "   planned migrations: $PLANNED (non-analytics: $OTHER)"
[ "$OTHER" = 0 ] || fail "non-analytics migrations planned (nothing changed)"
[ $((N0 + PLANNED)) = "$EXPECT" ] || fail "expected $EXPECT analytics migrations after upgrade, plan gives $((N0 + PLANNED)) (nothing changed)"
docker compose exec -T pretix grep -q "^\[pretix_event_analytics\]" /etc/pretix/pretix.cfg || fail "analytics salt missing from pretix.cfg"

echo "== $(date -u +%T) stop pretix, dump + fingerprint"
docker tag suti-pretix-pretix:latest $RB
docker compose stop pretix || fail "could not stop pretix"
docker compose exec -T db pg_dump -U postgres -d pretix -Fc > "$DUMP" || { docker compose start pretix; fail "dump failed, pretix restarted"; }
T=$(docker compose exec -T db pg_restore --list < "$DUMP" | grep -c "TABLE DATA"); echo "   dump: $(du -h "$DUMP" | cut -f1), $T tables"
[ "$T" -gt 100 ] || { docker compose start pretix; fail "dump looks incomplete, pretix restarted"; }
fp > "$FP"

echo "== $(date -u +%T) switch image and start"
docker tag $TAG suti-pretix-pretix:latest
docker compose up -d --no-build pretix || rollback "container did not start"
ready || rollback "pretix not answering"
V=$(docker compose exec -T pretix pip3 show pretix-event-analytics 2>/dev/null | awk '/^Version/{print $2}' | tr -d '\r')
echo "   running analytics $V"; [ "$V" = "$NEWV" ] || rollback "running analytics $V"
N=$(amig 'count(*)' '')
[ "$N" = "$EXPECT" ] || rollback "expected $EXPECT analytics migrations, found $N"
fp | diff "$FP" - || rollback "ticket data changed"
echo "   ticket data identical"
for p in /control/login /suti/ /suti/2026/; do s=$(status "$p"); echo "   $p -> $s"; [ "$s" = 200 ] || rollback "$p returned $s"; done

echo "== $(date -u +%T) keep ~/suti-upgrade/build describing production"
rm $U/build/$OW && cp $A/$NW $U/build/ && sed -i "s/$OW/$NW/g" $U/build/Dockerfile && grep -n analytics $U/build/Dockerfile
echo "== $(date -u +%T) UPGRADE OK   image=$TAG   rollback=$RB   dump=$DUMP"
