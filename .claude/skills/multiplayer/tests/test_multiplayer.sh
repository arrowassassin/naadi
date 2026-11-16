#!/usr/bin/env bash
# End-to-end test of multiplayer.py against a throwaway bare remote: lead + 2 agents in separate clones.
# Covers: init, plan/assign/accept/update/done/verify, concurrent posts (no lost messages), acks/inbox,
# path claims (exit 3 on overlap), shared-branch integrate race (both land) and conflict (exit 4 + auto-posted).
set -euo pipefail
MP="$(cd "$(dirname "$0")/../scripts" && pwd)/multiplayer.py"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
export MP_LEDGER_BRANCH=ledger MP_SHARED_BRANCH=shared
pass=0; fail=0
ok()   { echo "  PASS $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL $1"; fail=$((fail+1)); }
check(){ if eval "$2"; then ok "$1"; else bad "$1"; fi; }

git init -q --bare "$T/remote.git"
git clone -q "$T/remote.git" "$T/seed" 2>/dev/null
( cd "$T/seed" && echo "app" > app.txt && echo "shared" > shared.txt && git add . && git commit -qm init && git branch -M main && git push -q origin main && git push -q origin main:shared )
for who in lead a1 a2; do git clone -q "$T/remote.git" "$T/$who"; done
run() { local who=$1; shift; ( cd "$T/$who" && python3 "$MP" "$@" ); }

echo "1. init + hello"
run lead init >/dev/null
for w in lead a1 a2; do run $w hello --as $w --role "$( [ $w = lead ] && echo lead || echo implementer )" --purpose "test $w" >/dev/null; done
check "ledger branch exists on remote" "git --git-dir=$T/remote.git rev-parse -q --verify ledger >/dev/null"
check "three agents registered" "[ \$(git --git-dir=$T/remote.git ls-tree --name-only ledger agents/ | grep -c json) -eq 3 ]"

echo "2. task distribution"
run lead plan --as lead --subject "Wave A: a1 builds DB, a2 builds auth" --body "a2 depends on a1's profiles table" >/dev/null
T1=$(run lead assign --as lead --to a1 --key S02 --subject "DB migrations" --acceptance "migrations + RLS tests green" --paths "db/**")
T2=$(run lead assign --as lead --to a2 --key S03 --subject "Auth" --acceptance "login works" --paths "auth/**" --deps S02)
check "a1 inbox shows its task" "run a1 inbox --as a1 | grep -q 'DB migrations'"
check "a2 does not see a1's task" "! run a2 inbox --as a2 --type task | grep -q 'DB migrations'"
run a1 accept --as a1 S02 --subject "on it" >/dev/null
run a2 accept --as a2 S03 >/dev/null
run a1 update --as a1 S02 --subject "schema 60%, RLS next" >/dev/null
check "task board shows S02 in_progress" "run lead tasks | grep -E '^S02 +in_progress' >/dev/null"
check "S03 waits on S02" "run lead tasks | grep -E '^S03' | grep -q waits-on"
check "accepted task no longer in a1's inbox" "! run a1 inbox --as a1 --type task | grep -q 'DB migrations'"

echo "3. concurrent posts lose nothing"
for i in $(seq 1 6); do run a1 post --as a1 --type note --to all --subject "a1 note $i" >/dev/null & run a2 post --as a2 --type note --to all --subject "a2 note $i" >/dev/null & done; wait
n=$(cd "$T/lead" && python3 "$MP" inbox --as lead --all --type note --format json | wc -l)
check "all 12 concurrent notes present (got $n)" "[ $n -eq 12 ]"

echo "4. question / answer / ack"
Q=$(run a2 post --as a2 --type question --to a1 --subject "profiles.user_id type?" --body "uuid fk to auth.users?")
check "question in a1 inbox" "run a1 inbox --as a1 | grep -q 'profiles.user_id'"
run a1 post --as a1 --type answer --to a2 --re "$Q" --subject "uuid, fk auth.users on delete cascade" >/dev/null
check "replying acked the question" "! run a1 inbox --as a1 | grep -q 'profiles.user_id'"
check "wait finds the answer" "run a2 wait --as a2 --type answer --re $Q --timeout 30 --interval 1 | grep -q 'uuid, fk'"

echo "5. claims"
C=$(run a1 claim --as a1 --paths "db/migrations/**" --reason "writing 0001")
set +e; run a2 claim --as a2 --paths "db/migrations/0001.py" >/dev/null; rc=$?; set -e
check "overlapping claim refused with exit 3 (rc=$rc)" "[ $rc -eq 3 ]"
run a1 release --as a1 "$C" >/dev/null
check "claim succeeds after release" "run a2 claim --as a2 --paths db/migrations/0001.py >/dev/null"

echo "6. shared branch: race then conflict"
for w in a1 a2; do ( cd "$T/$w" && git fetch -q origin shared && git checkout -q -B work origin/shared ); done
( cd "$T/a1" && echo "a1" > a1.txt && git add . && git commit -qm "a1 work" )
( cd "$T/a2" && echo "a2" > a2.txt && git add . && git commit -qm "a2 work" )
run a1 integrate --as a1 --quiet >/dev/null & run a2 integrate --as a2 --quiet >/dev/null & wait
files=$(git --git-dir=$T/remote.git ls-tree --name-only shared)
check "both racing commits landed on shared" "echo '$files' | grep -q a1.txt && echo '$files' | grep -q a2.txt"
( cd "$T/a1" && git fetch -q origin shared && git reset -q --hard origin/shared && echo "a1 edit" > shared.txt && git commit -qam "a1 edits shared" )
( cd "$T/a2" && git fetch -q origin shared && git reset -q --hard origin/shared && echo "a2 edit" > shared.txt && git commit -qam "a2 edits shared" )
run a1 integrate --as a1 --quiet >/dev/null
set +e; run a2 integrate --as a2 --notify a1 >/dev/null; rc=$?; set -e
check "conflicting integrate exits 4 (rc=$rc)" "[ $rc -eq 4 ]"
check "conflict auto-posted to a1" "run a1 inbox --as a1 --type conflict | grep -q shared.txt"
check "a2 branch left clean (rebase aborted)" "[ -z \"\$(cd $T/a2 && git status --porcelain)\" ]"

echo "7. done / verify / board"
run a1 done --as a1 S02 --subject "migrations + RLS green" --commit abc1234 >/dev/null
check "done reaches the lead" "run lead inbox --as lead --type done | grep -q 'migrations + RLS green'"
run lead verify --as lead S02 >/dev/null
check "S02 verified on board" "run lead tasks | grep -E '^S02 +verified' >/dev/null"
board_out=$(run lead board); check "board renders" "echo \"$board_out\" | grep -q 'ACTIVE CLAIMS'"

echo; echo "passed $pass, failed $fail"; [ $fail -eq 0 ]
