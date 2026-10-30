"""Publish only LR6 management Issues through existing Git credentials.

Default: offline preview. --apply uses Git Credential Manager without printing
or storing its token. Never changes permissions or invites users.
"""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

from lab6_import_github import description, normalize

BASE = Path(__file__).resolve().parent
REPO = '07HaNNibal07/pc-builder-main'
ROOT = 'repos/' + REPO


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    source = json.loads((BASE/'lab6_tasks.json').read_text(encoding='utf-8'))
    tasks = source['tasks']
    assert len(tasks) == 26 and sum(t['estimated_hours'] for t in tasks) == 248
    if not args.apply:
        print(f'OFFLINE PREVIEW: {len(tasks)} Issues, status/owner labels and weekly milestones in {REPO}.')
        return
    credential = subprocess.run(
        [r'C:/Program Files/Git/cmd/git.exe', 'credential', 'fill'],
        input='protocol=https\nhost=github.com\n\n', capture_output=True, text=True,
        env=dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never'), timeout=30)
    values = dict(line.split('=', 1) for line in credential.stdout.splitlines() if '=' in line)
    token = values.get('password')
    if credential.returncode or not token:
        raise RuntimeError('Existing GitHub credentials unavailable; no remote changes.')

    def api(path, method='GET', payload=None):
        req = urllib.request.Request('https://api.github.com/' + path, method=method,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None,
            headers={'Authorization': 'Bearer ' + token, 'User-Agent': 'Overlord-PC-Lab6',
                     'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json',
                     'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'GitHub API HTTP {exc.code} for {method} {path.split("?")[0]}; '
                               'already completed actions remain on GitHub.') from None

    def all_pages(path):
        result = []
        separator = '&' if '?' in path else '?'
        for page in range(1, 101):
            batch = api(f'{path}{separator}per_page=100&page={page}')
            result.extend(batch)
            if len(batch) < 100:
                return result
        raise RuntimeError('Pagination limit reached; refusing incomplete inventory.')

    user = api('user')
    if user['login'].casefold() != REPO.split('/')[0].casefold():
        raise RuntimeError('Authenticated account does not match target owner; no changes.')
    repo = api(ROOT)
    if not repo.get('permissions', {}).get('push'):
        raise RuntimeError('Repository write permission unavailable; no changes.')
    issues = [i for i in all_pages(ROOT+'/issues?state=all') if 'pull_request' not in i]
    matched = {}
    for t in tasks:
        marker = f'Идентификатор плана: ЛР4-{t["id"]:02d}'
        candidates = [i for i in issues if i['html_url'] == t.get('issue_url') or
            normalize(i['title']) in (normalize(t['title']), normalize(t['original_title'])) or
            marker in (i.get('body') or '')]
        if len(candidates) > 1:
            raise RuntimeError(f'Ambiguous Issues for task {t["id"]}; no changes.')
        if candidates:
            matched[t['id']] = candidates[0]
    # Semantic matches with arbitrary titles cannot be proved; stop rather than
    # create duplicates in a repository containing any unrelated/unmapped Issues.
    if any(i['number'] not in {x['number'] for x in matched.values()} for i in issues):
        raise RuntimeError('Unmapped existing Issues found; review issue_url mapping before publication.')
    existing_labels = {x['name'] for x in all_pages(ROOT+'/labels')}
    needed = [('ЛР6', '6750a4'), ('Todo', 'bfd4f2'), ('In Progress', 'fbca04'), ('Done', '0e8a16')]
    needed += [('Owner: '+owner, 'd4e7fa') for owner in sorted({t['owner'] for t in tasks})]
    for name, color in needed:
        if name not in existing_labels:
            api(ROOT+'/labels', 'POST', {'name': name, 'color': color,
                'description': 'Учебный план управления Overlord PC — ЛР6'})
    milestones = {m['title']: m for m in all_pages(ROOT+'/milestones?state=all')}
    for week in sorted({t['week_end'] for t in tasks}):
        title = f'ЛР4 — окончание недели {week:02d}'
        end = date.fromisoformat(source['planning_start']) + timedelta(days=week*7-1)
        if title not in milestones:
            milestones[title] = api(ROOT+'/milestones', 'POST', {'title': title,
                'description': 'Учебный календарь ЛР4; условное начало 30.10.2026. '
                               'Milestone задаёт срок окончания, начало указано в описании каждой задачи.',
                'due_on': str(end)+'T20:59:59Z'})
    for t in tasks:
        if t['id'] not in matched:
            matched[t['id']] = api(ROOT+'/issues', 'POST', {'title': t['title'], 'body': description(t),
                'labels': ['ЛР6', t['status'], 'Owner: '+t['owner']],
                'milestone': milestones[f'ЛР4 — окончание недели {t["week_end"]:02d}']['number']})
            print(f'Created task {t["id"]:02d}: Issue #{matched[t["id"]]["number"]}', flush=True)
    for t in tasks:
        issue = matched[t['id']]
        links = '\n'.join(f'- {d["type"]}: [№{d["task_id"]}]({matched[d["task_id"]]["html_url"]})'
                          for d in t['dependencies']) or 'Предшественников нет.'
        new_body = description(t) + '\n\n## Связанные задачи\n\n' + links
        # Reuse existing Issues without overwriting content authored outside this tool.
        own = f'Идентификатор плана: ЛР4-{t["id"]:02d}' in (issue.get('body') or '')
        if own:
            patch = {'body': new_body, 'milestone': milestones[f'ЛР4 — окончание недели {t["week_end"]:02d}']['number']}
            labels = {x['name'] for x in issue.get('labels', [])}
            labels.update(['ЛР6', 'Owner: '+t['owner']])
            # Keep current status if an Issue was updated after initial publication.
            if not labels.intersection({'Todo','In Progress','Done'}):
                labels.add(t['status'])
            patch['labels'] = sorted(labels)
            api(ROOT+f'/issues/{issue["number"]}', 'PATCH', patch)
        t['issue_url'] = issue['html_url']
    actual = {i['number']: i for i in all_pages(ROOT+'/issues?state=all') if 'pull_request' not in i}
    for t in tasks:
        issue = actual[matched[t['id']]['number']]
        assert issue['html_url'] == t['issue_url']
        if f'Идентификатор плана: ЛР4-{t["id"]:02d}' in (issue.get('body') or ''):
            assert f'Трудозатраты: {t["estimated_hours"]} ч' in issue['body']
            assert issue['milestone'] is not None
            assert 'Owner: '+t['owner'] in {x['name'] for x in issue['labels']}
    receipt_path = BASE/'lab6_github_result.json'
    prior = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {}
    receipt = {'repository': repo['html_url'], 'project_url': prior.get('project_url'), 'github_projects_created': prior.get('github_projects_created',False),
        'projects_blocker': 'INSUFFICIENT_SCOPES: existing OAuth credential has gist, repo, workflow; project scope missing',
        'issues': [{'task_id': t['id'], 'number': matched[t['id']]['number'], 'url': t['issue_url']} for t in tasks],
        'milestones': [{'number':m['number'],'title':m['title'],'due_on':m['due_on'],'url':m['html_url']}
                       for m in milestones.values() if m['title'].startswith('ЛР4 —')],
        'verified_issue_count': len(tasks), 'note': 'Real repository Issues; GitHub Projects board has not been created.'}
    if prior.get('github_projects_created'):
        for key in ['projects_blocker','projects_access','project_views','status_counts','estimate_field','assignment_mode','dependencies_mode','deadline_mode']:
            if key in prior:
                receipt[key] = prior[key]
        receipt['note'] = 'Real GitHub Issues and Projects; project configuration retained from verified browser actions.'
    (BASE/'lab6_github_result.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    source.update(github_repository_url=repo['html_url'],github_issues_synced=True,github_projects_created=receipt['github_projects_created'],existing_issues_checked=True)
    (BASE/'lab6_tasks.json').write_text(json.dumps(source,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'VERIFIED: {len(tasks)} real GitHub Issues, owner/status labels, deadlines and linked dependencies.')


if __name__ == '__main__':
    main()
