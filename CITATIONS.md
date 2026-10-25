# Data, licences and citations

NeuroVoxel is **research / teaching software. It is not a medical device and gives no diagnosis or prognosis.**

## Dataset (NOT included in this repository)
**Medical Segmentation Decathlon (MSD), Task01_BrainTumour** — multimodal brain MRI (FLAIR, T1w, T1gd, T2w) with segmentation labels,
derived from the BraTS challenge data. Licence: **CC-BY-SA 4.0** (https://creativecommons.org/licenses/by-sa/4.0/).
Source: https://registry.opendata.aws/msd/ and http://medicaldecathlon.com/

* No MSD image or label file is stored in this repository; download the data yourself and mount it read-only (see README).
* If you share anything *derived from the images* (figures, screenshots, feature tables), give attribution, indicate changes and keep the CC-BY-SA 4.0 licence on that derived material.
* Suggested data acknowledgement: "Medical Segmentation Decathlon was accessed on <DATE> from https://registry.opendata.aws/msd."

Please cite:
1. Antonelli M, Reinke A, Bakas S, et al. **The Medical Segmentation Decathlon.** *Nature Communications* 13, 4128 (2022). doi:10.1038/s41467-022-30695-9
2. Simpson AL, Antonelli M, Bakas S, et al. **A large annotated medical image dataset for the development and evaluation of segmentation algorithms.** arXiv:1902.09063 (2019).

Because Task01 is derived from BraTS, also cite:
3. Menze BH, Jakab A, Bauer S, et al. **The Multimodal Brain Tumor Image Segmentation Benchmark (BRATS).** *IEEE Trans. Med. Imaging* 34(10):1993-2024 (2015). doi:10.1109/TMI.2014.2377694
4. Bakas S, Akbari H, Sotiras A, et al. **Advancing The Cancer Genome Atlas glioma MRI collections with expert segmentation labels and radiomic features.** *Scientific Data* 4:170117 (2017). doi:10.1038/sdata.2017.117
5. Bakas S, Reyes M, Jakab A, et al. **Identifying the best machine learning algorithms for brain tumor segmentation, progression assessment, and overall survival prediction in the BRATS challenge.** arXiv:1811.02629 (2018).

## Software and reference data used
| Component | Use | Licence | Cite / link |
|---|---|---|---|
| PyRadiomics | feature extraction (installed at build time from https://github.com/AIM-Harvard/pyradiomics) | BSD 3-Clause | van Griethuysen JJM et al. *Cancer Research* 77(21):e104-e107 (2017). doi:10.1158/0008-5472.CAN-17-0339 |
| PyRadiomics test data (`reference/pyradiomics_brain/`) | verification tab (brain1/brain2 + baseline CSVs) | BSD 3-Clause (`PYRADIOMICS_LICENSE.txt`) | same as above |
| IBSI digital phantom (`reference/ibsi_phantom/`) | verification against the standard | CC-BY 4.0 | Zwanenburg A et al. *Radiology* 295(2):328-338 (2020). doi:10.1148/radiol.2020191145 |
| Chart.js 4.4.7 (`static/vendor/`) | charts | MIT | https://www.chartjs.org |
| three.js r161 (`static/vendor/`) | 3D views | MIT | https://threejs.org |
| NiiVue (`static/vendor/`) | volume viewer | see its LICENSE (BSD-2-Clause) | https://github.com/niivue/niivue |
| SimpleITK, NumPy, SciPy, nibabel, pandas, openpyxl, FastAPI, Uvicorn | runtime | see each project | pinned in `requirements.txt` |
