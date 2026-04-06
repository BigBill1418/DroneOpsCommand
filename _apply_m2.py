#!/usr/bin/env python3
"""Apply all Milestone 2 patches and commit."""
import subprocess, os

os.chdir('/home/bbarnard065/droneops')

def patch(path, old, new):
    with open(path, 'r') as f:
        c = f.read()
    if old not in c:
        print(f"SKIP: {path}")
        return False
    with open(path, 'w') as f:
        f.write(c.replace(old, new, 1))
    print(f"OK: {path}")
    return True

# 1. MissionStatus enum expansion
patch('backend/app/models/mission.py',
    'DRAFT = "draft"\n    COMPLETED = "completed"',
    'DRAFT = "draft"\n    SCHEDULED = "scheduled"\n    IN_PROGRESS = "in_progress"\n    PROCESSING = "processing"\n    REVIEW = "review"\n    DELIVERED = "delivered"\n    COMPLETED = "completed"')

# 2. client_notes column
patch('backend/app/models/mission.py',
    '    download_link_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)\n    created_at',
    '    download_link_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)\n    client_notes: Mapped[str | None] = mapped_column(Text, nullable=True)\n    created_at')

# 3-4. Mission schemas
patch('backend/app/schemas/mission.py',
    '    download_link_expires_at: datetime | None = None\n\n\nclass MissionResponse',
    '    download_link_expires_at: datetime | None = None\n    client_notes: str | None = None\n\n\nclass MissionResponse')
patch('backend/app/schemas/mission.py',
    '    download_link_expires_at: datetime | None = None\n    created_at: datetime',
    '    download_link_expires_at: datetime | None = None\n    client_notes: str | None = None\n    created_at: datetime')

# 5. Client portal schema
patch('backend/app/schemas/client_portal.py',
    '    status: str\n    created_at: datetime\n    image_count: int = 0',
    '    status: str\n    client_notes: str | None = None\n    created_at: datetime\n    image_count: int = 0')

# 6. Migration
patch('backend/app/main.py',
    '("download_link_expires_at", "ALTER TABLE missions ADD COLUMN download_link_expires_at TIMESTAMP"),\n            ],',
    '("download_link_expires_at", "ALTER TABLE missions ADD COLUMN download_link_expires_at TIMESTAMP"),\n                ("client_notes", "ALTER TABLE missions ADD COLUMN client_notes TEXT"),\n            ],')

# 7. Version
patch('backend/app/main.py', 'version="2.57.0"', 'version="2.58.0"')

# 8. Router response
patch('backend/app/routers/client_portal.py',
    '        status=mission.status.value if hasattr(mission.status, "value") else str(mission.status),\n        created_at=mission.created_at,',
    '        status=mission.status.value if hasattr(mission.status, "value") else str(mission.status),\n        client_notes=mission.client_notes,\n        created_at=mission.created_at,')

# 9. Status colors
patch('frontend/src/components/shared/styles.ts',
    "  draft: 'yellow',\n  completed: 'cyan',\n  sent: 'green',",
    "  draft: 'gray',\n  scheduled: 'blue',\n  in_progress: 'yellow',\n  processing: 'orange',\n  review: 'cyan',\n  delivered: 'green',\n  completed: 'green',\n  sent: 'teal',")

# 10. Types
patch('frontend/src/api/types.ts',
    '  download_link_expires_at: string | null;\n  created_at: string;',
    '  download_link_expires_at: string | null;\n  client_notes: string | null;\n  created_at: string;')

# 11. App.tsx lazy import
patch('frontend/src/App.tsx',
    "const ClientLogin = lazy(() => import('./pages/client/ClientLogin'));",
    "const ClientLogin = lazy(() => import('./pages/client/ClientLogin'));\nconst ClientMissionDetail = lazy(() => import('./pages/client/ClientMissionDetail'));")

# 12. App.tsx route
patch('frontend/src/App.tsx',
    '<Route path="/client/login"',
    '<Route path="/client/mission/:missionId" element={<Suspense fallback={ClientFallback}><ClientMissionDetail /></Suspense>} />\n        <Route path="/client/login"')

# 13. Portal status colors
patch('frontend/src/pages/client/ClientPortal.tsx',
    "    draft: 'yellow',\n    completed: 'green',\n    sent: 'cyan',",
    "    draft: 'gray',\n    scheduled: 'blue',\n    in_progress: 'yellow',\n    processing: 'orange',\n    review: 'cyan',\n    delivered: 'green',\n    completed: 'green',\n    sent: 'teal',")

# 14. Clickable mission cards
patch('frontend/src/pages/client/ClientPortal.tsx',
    "                  (e.currentTarget as HTMLElement).style.borderColor = '#1a1f2e';\n                }}\n              >",
    "                  (e.currentTarget as HTMLElement).style.borderColor = '#1a1f2e';\n                }}\n                onClick={() => window.location.href = `/client/mission/${m.id}`}\n              >")

# 15-16. Version bumps
patch('frontend/package.json', '"version": "2.57.0"', '"version": "2.58.0"')
patch('README.md', '**Version 2.57.0**', '**Version 2.58.0**')

# 17. AppShell version
subprocess.run(['sed', '-i', 's/v2\\.57\\.0/v2.58.0/g', 'frontend/src/components/Layout/AppShell.tsx'])
print("OK: AppShell.tsx")

# Stage everything
subprocess.run(['git', 'add', '-A'])
r = subprocess.run(['git', 'diff', '--cached', '--stat'], capture_output=True, text=True)
print(f"\nStaged:\n{r.stdout}")
