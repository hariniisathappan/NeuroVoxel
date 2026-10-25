# NeuroVoxel — researcher setup and user guide

This document is a practical guide for installing and using NeuroVoxel on a researcher's own computer. It is separate from the scientific and developer details in `README.md`.

> **Research/teaching software only.** NeuroVoxel is not a medical device and is not for diagnosis, prognosis, or treatment decisions. A local installation does not replace institutional approval or security review for real patient data.

## At a glance

- NeuroVoxel is a local web interface backed by a FastAPI/Python service running in Docker.
- The browser address is `http://127.0.0.1:8000`. The current Compose configuration binds to `127.0.0.1`, so this setup is intended for use on the same computer.
- **Pipeline A (Upload a case)** accepts one or more MRI sequences and a matching segmentation, validates the files, then calculates radiomics and displays results. It does not require the MSD cohort for the upload calculation itself.
- The Dataset, cohort, and similar-reference-case features do require the MSD reference dataset.
- The reference dataset is not included in the NeuroVoxel project. Each researcher obtains it separately if needed.

## 1. Requirements

- Windows 10/11, macOS, or Linux with Docker Desktop (or Docker Engine and Docker Compose).
- Internet access for the initial Docker image build, which downloads a base image and Python dependencies. The first build may take several minutes. After a successful build, ordinary starts use the already-built image.
- Enough disk space for Docker, the database and staged results. If using the reference cohort, allow additional space for the separately downloaded dataset (the project README estimates about 7 GB).
- For patient data: authorization from the relevant institution and compliance with its ethics, consent, de-identification, storage, retention, and access rules.

## 2. Get the project

Obtain the project from the official repository or from the project author:

- [NeuroVoxel GitHub repository](https://github.com/hariniisathappan/NeuroVoxel)
- Keep the `LICENSE`, `CITATIONS.md`, `README.md`, and this guide with the project files.

If downloading a ZIP, extract the full project folder. The folder you use must contain `Dockerfile`, `docker-compose.yml`, and `Start-NeuroVoxel.bat` (Windows).

## 3. Choose whether to use the MSD cohort

### Option A — upload analysis only

You can use **Upload a case** without downloading MSD. In the existing `docker-compose.yml`, the MSD bind-mount line points to the project author's `E:` drive. On your computer, either change that line to your own MSD folder (Option B), or comment out/remove just that dataset-mount line if you do not have MSD:

```yaml
# - "E:/Dataset_tumor/Task01_BrainTumour:/data/msd:ro"
```

Keep the `neurovoxel_data:/var/lib/neurovoxel` volume. Without an MSD folder, the home page may display a **Dataset folder not found** notice because the cohort features have no mounted dataset. That notice concerns the reference cohort; use **Upload a case** for Pipeline A. The upload calculation does not use the MSD cohort. Similar-reference-case search and cohort functions will not be available without the cohort.

### Option B — upload analysis plus cohort comparisons

Download the MSD **Task01_BrainTumour** dataset from either source:

- [Medical Segmentation Decathlon — data page](https://medicaldecathlon.com/dataaws/)
- [AWS Open Data Registry — MSD](https://registry.opendata.aws/msd/)

Extract it outside the NeuroVoxel project folder. The folder mounted into the app must have `dataset.json`, `imagesTr`, and `labelsTr` directly inside it. For example:

```text
D:\ResearchData\Task01_BrainTumour\dataset.json
D:\ResearchData\Task01_BrainTumour\imagesTr\...
D:\ResearchData\Task01_BrainTumour\labelsTr\...
```

Edit the dataset mount line in `docker-compose.yml` to match the researcher's actual path. Keep the `:/data/msd:ro` ending so the dataset is mounted read-only. Example:

```yaml
- "D:/ResearchData/Task01_BrainTumour:/data/msd:ro"
```

Do not copy the dataset into the source-code folder or into the project database.

## 4. Start NeuroVoxel

### Windows

1. Start Docker Desktop and wait until it says Docker is running.
2. Open the NeuroVoxel project folder in File Explorer.
3. Click the address bar, type `powershell`, and press Enter. A PowerShell window opens in the project folder.
4. Start/build NeuroVoxel:

   ```powershell
   docker compose up -d --build
   ```

   Or double-click `Start-NeuroVoxel.bat` from File Explorer. The first build needs internet and may take several minutes.

5. Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in the browser.

### macOS / Linux

From a terminal in the project folder:

```bash
docker compose up -d --build
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

### Stop and start again

Stop the application while preserving the named data volume:

```powershell
docker compose down
```

Start it again after Docker Desktop is running:

```powershell
docker compose up -d
```

Do not add `-v` to `docker compose down` unless you intend to delete NeuroVoxel's persistent Docker volume and all the database/results stored in it.

## 5. Pipeline A — upload and analyze a case

1. Choose **Upload a case**.
2. Select the MRI sequences you have. Each sequence field accepts one `.nii` or `.nii.gz` volume. The segmentation is required.
3. Use sequences and a segmentation that represent the same registered 3D grid (matching dimensions, spacing, origin, and orientation). NeuroVoxel checks compatibility; it does not register, skull-strip, or resample the images for you.
4. Enter the segmentation label mapping as JSON. The left-hand keys are the label numbers present in the uploaded mask; values are the supported sub-region names `net`, `edema`, or `enhancing`. Example only:

   ```json
   {"1":"net","2":"edema","4":"enhancing"}
   ```

   Use the numbers actually present in your segmentation. Remove mappings for labels that are absent. Do not copy the example blindly if your mask uses different labels.

5. Select **Validate and calculate**. Review every validation warning as well as the pass/fail status. A successful calculation is not a clinical interpretation or a guarantee that input data are appropriate for every research question.
6. Review the reported region volumes and feature values. Use the viewer controls to inspect the images and segmentation. Uploads and computed results can be reopened from the saved-upload list while the local app's Docker volume is retained.
7. Use **Delete** in the app when an upload should be removed. Follow institutional policy for any other stored copies, exports, backups, or Docker-volume deletion.

Similarity to MSD reference cases is optional and is only meaningful when the comparison cohort and uploaded case have sufficiently comparable preprocessing, sequences, labels, and image characteristics. It is descriptive numeric similarity, not diagnosis.

## 6. Full-cohort workflow

When the MSD folder is mounted:

1. Go to **Dataset** and index the dataset once.
2. Go to **Explorer**, filter cases, then calculate individual cases or matching cases.
3. Open a calculated case to inspect features, visualizations, validation/verification, comparisons, sensitivity, and exports.
4. Use **Cohort analysis** for exploratory comparisons among calculated cases.

Indexing a dataset is not the same as calculating radiomics for every case. The app calculates radiomics on demand. See `README.md` for the methods, limits, and more detailed workflow.

## 7. Database, uploads, and backups

- The live database is `neurovoxel_v3.sqlite3` inside the Docker named volume `neurovoxel_data`, at `/var/lib/neurovoxel/` in the container. It is not necessarily the `nv.sqlite3` file in the project folder.
- Open [http://127.0.0.1:8000/db](http://127.0.0.1:8000/db) to view the live database summary in the app (read-only).
- On Windows, export a point-in-time SQLite copy by running from PowerShell in the project folder, while Docker Desktop and NeuroVoxel are running:

  ```powershell
  .\tools\Export-NeuroVoxelDatabase.ps1
  ```

  Open `database\neurovoxel_v3.sqlite3` in DB Browser for SQLite. Run the export again after new calculations or uploads to refresh that snapshot. The snapshot is not the live database and does not contain the MRI scans.
- The named Docker volume persists when the container is stopped or recreated. `docker compose down -v` removes the volume; treat that as permanent data deletion and verify the intended target first.
- Keep backups in an institution-approved location. Check exported reports, CSVs, screenshots, database copies, and logs for identifiers before sharing them.

## 8. Data handling and safety

The Compose file publishes the app on `127.0.0.1:8000`, intended for access from the same machine. Do not change this to `0.0.0.0`, expose it through a router/firewall/tunnel, or put it on public hosting if the intended deployment is workstation-local. The app does not provide a login system. Anyone with access to the workstation or Docker data may be able to access stored application data.

Only process images and segmentations you are authorized to use. Follow your institution's policies for human-subject research, consent or waiver, de-identification, access control, encryption, retention, backups, and deletion. Local execution is not by itself a legal or security approval.

## 9. Licenses and citations

Different parts of the project have separate licenses; do not treat them as one license:

- **NeuroVoxel source code:** MIT License — see the included [`LICENSE`](LICENSE). Preserve its copyright and license notice when redistributing the code.
- **MSD Task01_BrainTumour reference dataset:** this repository records it as **CC BY-SA 4.0**, not MIT and not plain CC BY 4.0. Dataset terms and attribution apply separately. The dataset is not bundled here; obtain it from its official [data page](https://medicaldecathlon.com/dataaws/) or [AWS registry](https://registry.opendata.aws/msd/). See the [CC BY-SA 4.0 deed](https://creativecommons.org/licenses/by-sa/4.0/).
- **IBSI digital phantom:** the project records its bundled phantom under **CC BY 4.0**. See the [CC BY 4.0 deed](https://creativecommons.org/licenses/by/4.0/) and the specific citation/license notices in `CITATIONS.md`.
- **Third-party software/assets:** PyRadiomics, SimpleITK, NumPy, SciPy, nibabel, pandas, FastAPI, Uvicorn, Chart.js, three.js, NiiVue, and other components retain their own terms. See `CITATIONS.md`, their included license files, and the actual package metadata. Before redistributing a built Docker image, check the licenses/notices for the exact versions and transitive dependencies included in that image.
- **Researcher-uploaded images:** CC licenses for MSD or the IBSI phantom do not grant rights to a researcher's own patient images. The uploader/institution must have the necessary authority and approvals.

Suggested citations for work using the MSD cohort are listed in `CITATIONS.md` and `README.md`. Cite NeuroVoxel and PyRadiomics when appropriate. Do not imply that the dataset authors endorse NeuroVoxel.

This guide is informational and not legal advice. No README or open-source license can guarantee that every deployment or image use complies with every institution's rules or local law. Ask the responsible institutional office to review the planned use and distribution.

## 10. Troubleshooting

### “Dataset folder not found”

The app cannot see `dataset.json` under its `/data/msd` mount. Check that the host folder really contains `dataset.json`, `imagesTr`, and `labelsTr` directly; confirm the Compose mount line points to that exact folder; then recreate the container to apply the mount:

```powershell
docker compose up -d --force-recreate
```

This preserves the named volume. For upload-only use without MSD, the notice can appear because the reference cohort is absent; Pipeline A upload analysis remains separate.

### Docker permission / connection error

Start Docker Desktop, wait until its engine is running, then reopen a regular PowerShell window and retry the Docker command. Do not double-click a `.ps1` script to run it as a document; run it from PowerShell.

### Page does not open

Check that Docker Desktop is running, then inspect container status and logs from the project folder:

```powershell
docker compose ps
docker compose logs --tail 100 neurovoxel
```

If port 8000 is already occupied, stop the other local service or ask the maintainer to configure a different host port.

### Database snapshot is missing or stale

Start the app and Docker Desktop, run the export script in Section 7 again, and check the printed output path. The exported file is a snapshot; refresh it after making new results.

## More information

- [Main README: scientific methods, limitations, and full project description](README.md)
- [Citations and license inventory](CITATIONS.md)
- [Live backend database viewer](http://127.0.0.1:8000/db) (available only while the app is running locally)
- [Docker Desktop installation](https://docs.docker.com/desktop/)
- [Docker Compose documentation](https://docs.docker.com/compose/)
- [DB Browser for SQLite](https://sqlitebrowser.org/)
