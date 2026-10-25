
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ git cmake ninja-build \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install build dependencies first.
RUN pip install --no-cache-dir \
    numpy==1.26.4 \
    setuptools wheel versioneer setuptools_scm \
    scikit-build-core cmake ninja pybind11

# Install PyRadiomics from GitHub, avoiding the broken PyPI 3.1.0 metadata.
RUN pip install --no-cache-dir --no-build-isolation \
    git+https://github.com/AIM-Harvard/pyradiomics.git

# Install the remaining application dependencies.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir numpy==1.26.4

# Stop the build if required imports fail.
RUN python -c "import numpy, SimpleITK, radiomics, nibabel, scipy; from radiomics import featureextractor; featureextractor.RadiomicsFeatureExtractor(); print('NumPy', numpy.__version__, '| PyRadiomics', radiomics.__version__, 'OK')"

COPY . .

RUN useradd --create-home --uid 10001 neurovoxel \
    && mkdir -p /var/lib/neurovoxel \
    && chown -R neurovoxel:neurovoxel /app /var/lib/neurovoxel

USER neurovoxel

ENV NEUROVOXEL_DATA_DIR=/var/lib/neurovoxel \
    NV_DATASET_DIR=/data/msd

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
