# Local database snapshots

The live NeuroVoxel database is stored in the persistent Docker volume `neurovoxel_data`.
To make an up-to-date copy that can be opened in DB Browser for SQLite, run
`tools\Export-NeuroVoxelDatabase.ps1` from PowerShell while Docker Desktop and NeuroVoxel are running.

The script creates `database\neurovoxel_v3.sqlite3`. This is a point-in-time snapshot;
run the script again after new indexing, calculations, or uploads to refresh it. Pipeline A
uploads appear in `uploaded_cases` and `uploaded_feature_values`, separately from the
reference dataset's `cases` and `feature_values` tables. The web page at
`http://127.0.0.1:8000/db` always reads the live database.

Dataset MRI files do not belong in this folder. Keep `Task01_BrainTumour` separately;
it should contain `dataset.json`, `imagesTr`, and `labelsTr`. Set its host path in
`docker-compose.yml`. Docker mounts the dataset read-only into the app.
