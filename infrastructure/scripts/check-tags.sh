#!/usr/bin/env bash
# Live check of the App cost tag (#669), run by the owner with their own AWS
# profile. Read-only: it only calls describe*, list* and get* APIs.
# Runbook: _docs/runbooks/terraform.md "Tag check".
#
#   AWS_PROFILE=jsolisdev infrastructure/scripts/check-tags.sh
#
# A resource is Classora's when it is tagged Project=classora, has a classora
# name or ARN prefix (classora-* buckets, IAM roles, users, instance profiles,
# SSM documents and EBS names; classora/* ECR; /classora/* SSM parameters; log
# groups containing "classora"), or lives in the Classora VPC. Regional resources
# are read in us-east-2, global ones (IAM, Route 53, the budget) via us-east-1.
# Expected App by the Environment tag: dev -> classora-dev, prod -> classora-prod,
# bootstrap, shared and monitoring -> classora-shared.
#
# Output, one line per finding, then a summary:
#   MISSING <arn-or-id> <name>
#   WRONG <arn-or-id> App=<got> expected=<want>
#   PENDING <snapshot-id> pre-apply DLM snapshot, expires <StartTime+8d>
#   EXCEPTION <id> <reason>              (VPC default SG, its rules, NACL, main route table)
#   checked=N ok=N missing=N wrong=N pending=N exceptions=N
# Exit 0 when missing=0 and wrong=0; 1 otherwise; 2 on usage or credential errors.
# E-mail identities are printed as ***@<domain> (the output goes on public issues).
# Needs AWS CLI v2 and bash (3.2 is enough); no jq.
set -euo pipefail

if [ $# -gt 0 ]; then
  echo "usage: AWS_PROFILE=<profile> check-tags.sh (no arguments)" >&2
  exit 2
fi

export AWS_PROFILE=${AWS_PROFILE:-jsolisdev}
export AWS_PAGER=""
regional=us-east-2
global=us-east-1
# Days a DLM snapshot taken before the apply may stay untagged: retention is 7
# daily snapshots, so every pre-apply one is gone 8 days after it was taken (D4).
pending_days=8

sep=$(printf '\037')
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
records="$work/records"
seen="$work/seen"
: >"$records"
: >"$seen"

# aws with text output, tab separators turned into \037 so empty fields survive
# `read`. Any failure is a credential or permission problem: exit 2.
awsq() {
  local out="$work/out" err="$work/err"
  if ! aws "$@" --output text >"$out" 2>"$err"; then
    echo "check-tags.sh: aws $1 $2 failed:" >&2
    cat "$err" >&2
    exit 2
  fi
  tr '\t' "$sep" <"$out"
}

none() {
  if [ "$1" = None ]; then printf ''; else printf '%s' "$1"; fi
}

# Dedupe key: EC2 IDs are unique on their own, so arn:aws:ec2:...:<type>/<id>
# and a describe call's <id> meet on the same key.
key_of() {
  case "$1" in
    arn:aws:ec2:*) printf '%s' "${1##*/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

is_seen() {
  grep -Fxq -- "$(key_of "$1")" "$seen"
}

# add <id> <name> <project> <environment> <app> <kind> [<extra>]
# kind: plain | dlmsnap (extra = StartTime) | exception (extra = reason)
add() {
  local key
  key=$(key_of "$1")
  if grep -Fxq -- "$key" "$seen"; then return 0; fi
  printf '%s\n' "$key" >>"$seen"
  printf '%s\037%s\037%s\037%s\037%s\037%s\037%s\n' "$1" "$(none "$2")" "$(none "$3")" \
    "$(none "$4")" "$(none "$5")" "$6" "${7:-}" >>"$records"
}

tagq="Tags[?Key=='Name']|[0].Value, Tags[?Key=='Project']|[0].Value, Tags[?Key=='Environment']|[0].Value, Tags[?Key=='App']|[0].Value"

# Prints the Name, Project, Environment and App values of a tag listing query
# result (Key/Value pairs) on one \037-separated line.
tag_values() {
  local name="" project="" environment="" app="" k v
  while IFS="$sep" read -r k v; do
    case "$k" in
      Name) name=$v ;;
      Project) project=$v ;;
      Environment) environment=$v ;;
      App) app=$v ;;
    esac
  done
  printf '%s\037%s\037%s\037%s\n' "${name:-None}" "${project:-None}" "${environment:-None}" "${app:-None}"
}

if ! aws sts get-caller-identity --query Account --output text >"$work/account" 2>"$work/err"; then
  echo "check-tags.sh: no AWS credentials for profile $AWS_PROFILE:" >&2
  cat "$work/err" >&2
  exit 2
fi

# --- The Classora VPC and the security groups in it.
awsq ec2 describe-vpcs --region "$regional" --filters Name=tag:Project,Values=classora \
  --query 'Vpcs[].[VpcId]' >"$work/vpcs"
vpcs=$(tr '\n' ',' <"$work/vpcs" | sed 's/,$//')

# --- EC2, us-east-2. Snapshots first: they carry StartTime and the DLM marker.
awsq ec2 describe-snapshots --region "$regional" --owner-ids self \
  --query "Snapshots[].[SnapshotId, StartTime, Tags[?Key=='aws:dlm:lifecycle-policy-id']|[0].Value, $tagq]" \
  >"$work/snapshots"
while IFS="$sep" read -r id start dlm name project environment app; do
  [ "$project" = classora ] || case "$name" in classora-*) ;; *) continue ;; esac
  if [ "$dlm" != None ]; then
    add "$id" "$name" "$project" "$environment" "$app" dlmsnap "$start"
  else
    add "$id" "$name" "$project" "$environment" "$app" plain
  fi
done <"$work/snapshots"

awsq ec2 describe-volumes --region "$regional" --query "Volumes[].[VolumeId, $tagq]" >"$work/volumes"
while IFS="$sep" read -r id name project environment app; do
  [ "$project" = classora ] || case "$name" in classora-*) ;; *) continue ;; esac
  add "$id" "$name" "$project" "$environment" "$app" plain
done <"$work/volumes"

awsq ec2 describe-instances --region "$regional" \
  --filters Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query "Reservations[].Instances[].[InstanceId, VpcId, $tagq]" >"$work/instances"
while IFS="$sep" read -r id vpc name project environment app; do
  [ "$project" = classora ] || case ",$vpcs," in *",$vpc,"*) ;; *) continue ;; esac
  add "$id" "$name" "$project" "$environment" "$app" plain
done <"$work/instances"

: >"$work/groups"
if [ -n "$vpcs" ]; then
  awsq ec2 describe-security-groups --region "$regional" --filters "Name=vpc-id,Values=$vpcs" \
    --query "SecurityGroups[].[GroupId, GroupName, $tagq]" >"$work/groups"
  awsq ec2 describe-network-acls --region "$regional" --filters "Name=vpc-id,Values=$vpcs" \
    --query "NetworkAcls[].[NetworkAclId, IsDefault, $tagq]" >"$work/acls"
  awsq ec2 describe-route-tables --region "$regional" --filters "Name=vpc-id,Values=$vpcs" \
    --query "RouteTables[].[RouteTableId, length(Associations[?Main]), $tagq]" >"$work/routes"
else
  : >"$work/acls"
  : >"$work/routes"
fi
default_groups=""
while IFS="$sep" read -r id group_name name project environment app; do
  if [ "$group_name" = default ] && [ "$project" != classora ]; then
    default_groups="$default_groups $id"
    add "$id" "$name" "$project" "$environment" "$app" exception "VPC default security group (D10)"
  else
    add "$id" "${name/#None/$group_name}" "$project" "$environment" "$app" plain
  fi
done <"$work/groups"

group_ids=$(cut -d "$sep" -f 1 <"$work/groups" | tr '\n' ',' | sed 's/,$//')
if [ -n "$group_ids" ]; then
  awsq ec2 describe-security-group-rules --region "$regional" --filters "Name=group-id,Values=$group_ids" \
    --query "SecurityGroupRules[].[SecurityGroupRuleId, GroupId, $tagq]" >"$work/rules"
  while IFS="$sep" read -r id group name project environment app; do
    case " $default_groups " in
      *" $group "*) add "$id" "$name" "$project" "$environment" "$app" exception "rule of the VPC default security group $group (D10)" ;;
      *) add "$id" "$name" "$project" "$environment" "$app" plain ;;
    esac
  done <"$work/rules"
fi

while IFS="$sep" read -r id is_default name project environment app; do
  if [ "$is_default" = True ] && [ "$project" != classora ]; then
    add "$id" "$name" "$project" "$environment" "$app" exception "VPC default network ACL (D10)"
  else
    add "$id" "$name" "$project" "$environment" "$app" plain
  fi
done <"$work/acls"

while IFS="$sep" read -r id main name project environment app; do
  if [ "$main" != 0 ] && [ "$project" != classora ]; then
    add "$id" "$name" "$project" "$environment" "$app" exception "VPC main route table (D10)"
  else
    add "$id" "$name" "$project" "$environment" "$app" plain
  fi
done <"$work/routes"

# --- Everything the tagging API lists (tagged resources of every type), both regions.
for region in "$regional" "$global"; do
  awsq resourcegroupstaggingapi get-resources --region "$region" \
    --query "ResourceTagMappingList[].[ResourceARN, $tagq]" >"$work/tagged"
  while IFS="$sep" read -r arn name project environment app; do
    if [ "$project" != classora ]; then
      case "$arn" in
        *:classora-* | */classora-* | */classora/* | *:log-group:*classora*) ;;
        *) continue ;;
      esac
    fi
    add "$arn" "${name/#None/${arn##*[:/]}}" "$project" "$environment" "$app" plain
  done <"$work/tagged"
done

# --- IAM (global; the tagging API does not list roles or users).
awsq iam list-roles --query "Roles[?starts_with(RoleName, 'classora-')].[Arn, RoleName]" >"$work/roles"
while IFS="$sep" read -r arn role; do
  is_seen "$arn" && continue
  values=$(awsq iam list-role-tags --role-name "$role" --query 'Tags[].[Key, Value]' | tag_values)
  IFS="$sep" read -r _ project environment app <<<"$values"
  add "$arn" "$role" "$project" "$environment" "$app" plain
done <"$work/roles"

awsq iam list-users --query "Users[?starts_with(UserName, 'classora-')].[Arn, UserName]" >"$work/users"
while IFS="$sep" read -r arn user; do
  is_seen "$arn" && continue
  values=$(awsq iam list-user-tags --user-name "$user" --query 'Tags[].[Key, Value]' | tag_values)
  IFS="$sep" read -r _ project environment app <<<"$values"
  add "$arn" "$user" "$project" "$environment" "$app" plain
done <"$work/users"

awsq iam list-instance-profiles \
  --query "InstanceProfiles[?starts_with(InstanceProfileName, 'classora-')].[Arn, InstanceProfileName]" \
  >"$work/profiles"
while IFS="$sep" read -r arn profile; do
  is_seen "$arn" && continue
  values=$(awsq iam list-instance-profile-tags --instance-profile-name "$profile" \
    --query 'Tags[].[Key, Value]' | tag_values)
  IFS="$sep" read -r _ project environment app <<<"$values"
  add "$arn" "$profile" "$project" "$environment" "$app" plain
done <"$work/profiles"

# --- CloudWatch log groups (none exist today; flags any that appear).
for region in "$regional" "$global"; do
  awsq logs describe-log-groups --region "$region" \
    --query "logGroups[?contains(logGroupName, 'classora')].[logGroupArn, logGroupName]" >"$work/logs"
  while IFS="$sep" read -r arn group; do
    is_seen "$arn" && continue
    values=$(awsq logs list-tags-for-resource --region "$region" --resource-arn "$arn" \
      --query "[tags.Name, tags.Project, tags.Environment, tags.App]")
    IFS="$sep" read -r _ project environment app <<<"$values"
    add "$arn" "$group" "$project" "$environment" "$app" plain
  done <"$work/logs"
done

# --- ECR repositories, S3 buckets, SSM parameters and documents: names the
# tagging API missed (untagged resources).
awsq ecr describe-repositories --region "$regional" \
  --query "repositories[?starts_with(repositoryName, 'classora/')].[repositoryArn, repositoryName]" \
  >"$work/repos"
while IFS="$sep" read -r arn repo; do
  is_seen "$arn" && continue
  values=$(awsq ecr list-tags-for-resource --region "$regional" --resource-arn "$arn" \
    --query 'tags[].[Key, Value]' | tag_values)
  IFS="$sep" read -r _ project environment app <<<"$values"
  add "$arn" "$repo" "$project" "$environment" "$app" plain
done <"$work/repos"

awsq s3api list-buckets --query "Buckets[?starts_with(Name, 'classora-')].[Name]" >"$work/buckets"
while IFS="$sep" read -r bucket; do
  is_seen "arn:aws:s3:::$bucket" && continue
  # A bucket without tags answers NoSuchTagSet: that is "no tags", not an error.
  if aws s3api get-bucket-tagging --region "$regional" --bucket "$bucket" \
    --query 'TagSet[].[Key, Value]' --output text >"$work/bucket-tags" 2>"$work/err"; then
    values=$(tr '\t' "$sep" <"$work/bucket-tags" | tag_values)
  elif grep -q NoSuchTagSet "$work/err"; then
    values=$(tag_values </dev/null)
  else
    echo "check-tags.sh: aws s3api get-bucket-tagging failed for $bucket:" >&2
    cat "$work/err" >&2
    exit 2
  fi
  IFS="$sep" read -r _ project environment app <<<"$values"
  add "arn:aws:s3:::$bucket" "$bucket" "$project" "$environment" "$app" plain
done <"$work/buckets"

awsq ssm describe-parameters --region "$regional" \
  --parameter-filters Key=Name,Option=BeginsWith,Values=/classora/ --query 'Parameters[].[ARN, Name]' \
  >"$work/parameters"
while IFS="$sep" read -r arn parameter; do
  is_seen "$arn" && continue
  values=$(awsq ssm list-tags-for-resource --region "$regional" --resource-type Parameter \
    --resource-id "$parameter" --query 'TagList[].[Key, Value]' | tag_values)
  IFS="$sep" read -r _ project environment app <<<"$values"
  add "$arn" "$parameter" "$project" "$environment" "$app" plain
done <"$work/parameters"

account=$(cat "$work/account")
awsq ssm list-documents --region "$regional" --filters Key=Owner,Values=Self \
  --query "DocumentIdentifiers[?starts_with(Name, 'classora-')].[Name, $tagq]" >"$work/documents"
while IFS="$sep" read -r document _ project environment app; do
  add "arn:aws:ssm:$regional:$account:document/$document" "$document" "$project" "$environment" "$app" plain
done <"$work/documents"

# --- Classify.
expected_app() {
  case "$1" in
    dev) echo classora-dev ;;
    prod) echo classora-prod ;;
    bootstrap | shared | monitoring) echo classora-shared ;;
    *) echo "" ;;
  esac
}

# Date N days after an ISO-8601 instant, with GNU date or BSD (macOS) date.
plus_days() {
  local instant=${1%%.*} days=$2
  instant=${instant%%+*}
  instant=${instant%Z}
  if date --version >/dev/null 2>&1; then
    date -u -d "$instant UTC +$days days" +%Y-%m-%dT%H:%M:%SZ
  else
    date -u -j -v+"$days"d -f %Y-%m-%dT%H:%M:%S "$instant" +%Y-%m-%dT%H:%M:%SZ
  fi
}
now=$(date -u +%Y-%m-%dT%H:%M:%SZ)

checked=0 ok=0 missing=0 wrong=0 pending=0 exceptions=0
while IFS="$sep" read -r id name _ environment app kind extra; do
  checked=$((checked + 1))
  # The output goes on a public issue: an SES e-mail identity keeps only its domain.
  case "$id" in *@*) id="${id%/*}/***@${id##*@}" ;; esac
  case "$name" in *@*) name="***@${name##*@}" ;; esac
  if [ "$kind" = exception ]; then
    echo "EXCEPTION $id $extra"
    exceptions=$((exceptions + 1))
    continue
  fi
  want=$(expected_app "$environment")
  if [ -z "$app" ]; then
    if [ "$kind" = dlmsnap ]; then
      expires=$(plus_days "$extra" "$pending_days")
      if [[ "$expires" > "$now" ]]; then
        echo "PENDING $id pre-apply DLM snapshot, expires $expires"
        pending=$((pending + 1))
        continue
      fi
    fi
    echo "MISSING $id ${name:--}"
    missing=$((missing + 1))
  elif [ -n "$want" ] && [ "$app" != "$want" ]; then
    echo "WRONG $id App=$app expected=$want"
    wrong=$((wrong + 1))
  elif [ -z "$want" ]; then
    # No Environment tag to derive the value from: any of the three values passes.
    case "$app" in
      classora-dev | classora-prod | classora-shared) ok=$((ok + 1)) ;;
      *)
        echo "WRONG $id App=$app expected=classora-dev|classora-prod|classora-shared"
        wrong=$((wrong + 1))
        ;;
    esac
  else
    ok=$((ok + 1))
  fi
done <"$records"

echo "checked=$checked ok=$ok missing=$missing wrong=$wrong pending=$pending exceptions=$exceptions"
[ "$missing" -eq 0 ] && [ "$wrong" -eq 0 ]
