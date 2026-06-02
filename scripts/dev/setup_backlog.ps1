# HOST (Windows dev) - one-time setup of the GitHub Issues backlog for Overwatch:
# creates the label taxonomy (docs/GROOMING.md) and the V1 milestone. Idempotent
# (re-running updates labels in place). Requires gh authenticated to
# Dexom-GH/overwatch.
#   Usage:  ./scripts/dev/setup_backlog.ps1
$ErrorActionPreference = "Stop"

# Resolve gh (PATH, else the default Windows install location).
$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = "C:\Program Files\GitHub CLI\gh.exe" }
if (-not (Test-Path $gh)) { throw "gh CLI not found. Install GitHub CLI or fix the path in this script." }

$repo = "Dexom-GH/overwatch"

# Each label: name, color (hex, no #), description.
$labels = @(
  @("type:spike",            "d73a4a", "Timeboxed investigation to de-risk an unknown"),
  @("type:slice",            "0e8a16", "Vertical, demoable feature slice"),
  @("type:chore",            "c5def5", "Infra / tooling / maintenance"),
  @("type:bug",              "b60205", "Defect"),
  @("type:decision",         "5319e7", "Needs or affects an ADR decision"),
  @("area:capture",          "1d76db", "Capture stage (ZED RGB and depth)"),
  @("area:inference",        "1d76db", "Inference (DeepStream detect/track, ReID, pose)"),
  @("area:fusion",           "1d76db", "Fusion / logic (depth fusion, counts, health, events)"),
  @("area:output",           "1d76db", "Output (Slack, store, dashboard)"),
  @("area:bus",              "1d76db", "Message bus contract (schemas, topics)"),
  @("area:infra",            "0052cc", "Jetson env / provisioning / models"),
  @("area:ops",              "0052cc", "Claude tooling (skills, agents, workflows)"),
  @("prio:P0",               "b60205", "Must-have for the next milestone"),
  @("prio:P1",               "fbca04", "Important, not blocking"),
  @("prio:P2",               "0e8a16", "Nice to have / later"),
  @("status:needs-grooming", "ededed", "Not yet Ready (see docs/GROOMING.md)"),
  @("status:ready",          "0e8a16", "Meets Definition of Ready; implementable"),
  @("status:blocked",        "d93f0b", "Blocked by a dependency"),
  @("v1",                    "0e8a16", "In V1 scope"),
  @("v2",                    "ededed", "Deferred to V2"),
  @("v2-fwd",                "fbca04", "V2 feature pulled forward into V1"),
  @("risk:high",             "d73a4a", "High-risk / feasibility-critical")
)

Write-Host "== creating/updating labels on $repo =="
foreach ($l in $labels) {
  & $gh label create $l[0] --repo $repo --color $l[1] --description $l[2] --force
}

$milestoneTitle = "V1 - Animal Monitoring MVP"
Write-Host "== ensuring milestone '$milestoneTitle' =="
$existing = & $gh api "repos/$repo/milestones?state=all" --jq ".[].title"
if ($existing -contains $milestoneTitle) {
  Write-Host "milestone already exists - skipping"
} else {
  $desc = "Animal monitoring MVP: counting, vision-only ID, health. See docs/ROADMAP_V1_V2.md."
  & $gh api "repos/$repo/milestones" -f "title=$milestoneTitle" -f "description=$desc" | Out-Null
  Write-Host "milestone created"
}

Write-Host "== backlog setup complete =="
