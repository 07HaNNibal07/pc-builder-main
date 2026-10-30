"""Import the lab plan with GitHub CLI. Preview is offline and is the default.

Creates draft project items, never Issues or invitations. No application upload.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

BASE = Path(__file__).resolve().parent


def gh(*args):
    result = subprocess.run(['gh', *map(str, args)], capture_output=True, encoding='utf-8')
    if result.returncode:
        # Avoid copying arbitrary provider diagnostics or credentials into reports.
        raise RuntimeError('GitHub CLI command failed: gh ' + ' '.join(map(str, args[:2])) +
                           '. Check login, project scope and permissions in your terminal.')
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()


def normalize(title):
    return re.sub(r'^\[ЛР4-\d+\]\s*', '', title).casefold().strip()


def description(t):
    deps = '; '.join(f'№{d["task_id"]} {d["type"]}' for d in t['dependencies']) or 'Нет'
    return '\n'.join([f'Идентификатор плана: ЛР4-{t["id"]:02d}',
        f'Ответственный: {t["owner"]}', f'Период: {t["start_date"]} — {t["end_date"]} (учебный календарь)',
        f'Учебные недели: {t["week_start"]}–{t["week_end"]}', f'Трудозатраты: {t["estimated_hours"]} ч',
        f'Зависимости: {deps}', f'Исходное название ЛР4: {t["original_title"]}', '',
        '## Описание', t['description'], '', '## Проверка состояния', t['evidence'], '',
        '## Критерии приёмки', *['- [ ] '+c for c in t['acceptance_criteria']]])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true', help='Actually create/update GitHub project items')
    p.add_argument('--repo', help='owner/name; default: authenticated-user/overlord-pc-lab6-management')
    p.add_argument('--project-number', type=int, help='Reuse an existing personal project')
    a = p.parse_args()
    data = json.loads((BASE/'lab6_tasks.json').read_text(encoding='utf-8'))
    tasks = data['tasks']
    assert len(tasks)==26 and sum(t['estimated_hours'] for t in tasks)==248
    if not a.apply:
        print('OFFLINE PREVIEW. No GitHub changes.')
        print('26 items; 248 hours; 2026-10-30 — 2027-02-04; owners by full name.')
        print('Creates/reuses a private management repository and personal Overlord PC project.')
        print('Reuses exact matching Issues, otherwise creates drafts; never creates Issues.')
        for t in tasks:
            print(f'{t["id"]:02d} | {t["owner"]} | {t["estimated_hours"]} h | {t["status"]}')
        return
    if not shutil.which('gh'):
        raise RuntimeError('GitHub CLI gh is not installed. Offline artifacts remain available.')
    login = gh('api', 'user')['login']
    repo = a.repo or login+'/overlord-pc-lab6-management'
    if repo.split('/')[0].casefold()!=login.casefold():
        raise RuntimeError('This importer supports only the authenticated user personal namespace.')
    # Complete read preflight before creating remote resources.
    repos = gh('repo', 'list', login, '--limit', '1000', '--json', 'nameWithOwner')
    if len(repos)>=1000:
        raise RuntimeError('Repository inventory reached the limit; refusing incomplete discovery.')
    exists = any(r['nameWithOwner'].casefold()==repo.casefold() for r in repos)
    issues = gh('issue','list','--repo',repo,'--state','all','--limit','1000',
                '--json','title,url,body') if exists else []
    if len(issues)>=1000:
        raise RuntimeError('Issue inventory reached the limit; refusing incomplete discovery.')
    matches = {}
    for t in tasks:
        exact = [i for i in issues if i['url']==t.get('issue_url') or
                 normalize(i['title']) in (normalize(t['title']), normalize(t['original_title'])) or
                 f'Идентификатор плана: ЛР4-{t["id"]:02d}' in (i.get('body') or '')]
        if len(exact)>1:
            raise RuntimeError(f'Ambiguous existing Issues for task {t["id"]}; no mutations performed.')
        if exact:
            matches[t['id']]=exact[0]['url']
    if issues and len(matches)<26:
        raise RuntimeError('Existing repository has unmapped Issues. Set issue_url in JSON for each corresponding task, '
                           'or use the default separate management repository. No mutations performed.')
    projects = gh('project','list','--owner',login,'--limit','1000','--format','json')['projects']
    if len(projects)>=1000:
        raise RuntimeError('Project inventory reached the limit; refusing incomplete discovery.')
    selected = [x for x in projects if x['number']==a.project_number] if a.project_number else [x for x in projects if x['title']=='Overlord PC' and not x.get('closed')]
    if len(selected)>1 or (a.project_number and not selected):
        raise RuntimeError('Project selection is ambiguous or unavailable; no mutations performed.')
    if not exists:
        gh('repo','create',repo,'--private','--description','Overlord PC — lab 6 project management, 26 LR4 tasks')
    project = selected[0] if selected else gh('project','create','--owner',login,'--title','Overlord PC','--format','json')
    number=project['number']
    project=gh('project','view',number,'--owner',login,'--format','json')
    gh('project','link',number,'--owner',login,'--repo',repo)
    schema=gh('project','field-list',number,'--owner',login,'--limit','100','--format','json')['fields']
    fields={f['name']:f for f in schema}
    for name,kind in [('Owner','TEXT'),('Estimate (h)','NUMBER'),('Start date','DATE'),('End date','DATE'),('Dependencies','TEXT'),('LR4 ID','NUMBER')]:
        if name not in fields:
            fields[name]=gh('project','field-create',number,'--owner',login,'--name',name,'--data-type',kind,'--format','json')
    if 'Status' not in fields:
        fields['Status']=gh('project','field-create',number,'--owner',login,'--name','Status','--data-type','SINGLE_SELECT','--single-select-options','Todo,In Progress,Done','--format','json')
    status={o['name']:o['id'] for o in fields['Status'].get('options',[])}
    if not {'Todo','In Progress','Done'}<=status.keys():
        raise RuntimeError('Configure Status options as Todo / In Progress / Done, then rerun. No items imported yet.')
    items=gh('project','item-list',number,'--owner',login,'--limit','10000','--format','json')['items']
    if len(items)>=10000:
        raise RuntimeError('Project item inventory reached limit; no items imported.')
    results=[]
    for t in tasks:
        prefix=f'[ЛР4-{t["id"]:02d}]'
        url=matches.get(t['id'])
        candidates=[i for i in items if (url and i.get('content',{}).get('url')==url) or i.get('title','').startswith(prefix)]
        if len(candidates)>1:
            raise RuntimeError('Duplicate project markers detected; refusing to create another item.')
        item=candidates[0] if candidates else (
            gh('project','item-add',number,'--owner',login,'--url',url,'--format','json') if url else
            gh('project','item-create',number,'--owner',login,'--title',t['title'],'--body',description(t),'--format','json'))
        base=['project','item-edit','--id',item['id'],'--project-id',project['id']]
        values=[('Owner','--text',t['owner']),('Estimate (h)','--number',t['estimated_hours']),
                ('Start date','--date',t['start_date']),('End date','--date',t['end_date']),
                ('Dependencies','--text','; '.join(f'№{d["task_id"]} {d["type"]}' for d in t['dependencies']) or 'Нет'),
                ('LR4 ID','--number',t['id']),('Status','--single-select-option-id',status[t['status']])]
        for field,flag,value in values:
            gh(*base,'--field-id',fields[field]['id'],flag,value)
        results.append({'task_id':t['id'],'item_id':item['id'],'issue_url':url})
        print(f'Imported {prefix}')
    # Verify all markers/linked issue IDs before claiming completion.
    actual=gh('project','item-list',number,'--owner',login,'--limit','10000','--format','json')['items']
    if not {r['item_id'] for r in results}<={i['id'] for i in actual}:
        raise RuntimeError('Import verification failed; inspect the project before rerunning.')
    result={'repository':'https://github.com/'+repo,'project_url':project['url'],'items':results,
            'note':'Configure Board view by Status manually; Owner is text, not GitHub Assignees.'}
    (BASE/'lab6_github_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(project['url'])


if __name__=='__main__':
    try:
        main()
    except (RuntimeError, AssertionError, KeyError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(1)
