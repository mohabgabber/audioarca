from pathlib import Path
import os
import urllib.request


FASTTEXT_URL = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin"


def ensure_fasttext(root: Path) -> None:
    target = root / "fasttext" / "lid.176.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    urllib.request.urlretrieve(FASTTEXT_URL, target)


def ensure_whisper(root: Path) -> None:
    from faster_whisper import WhisperModel

    size = os.getenv("FORENSICS_WHISPER_MODEL_SIZE", "small")
    model_root = root / "whisper"
    model_root.mkdir(parents=True, exist_ok=True)
    WhisperModel(size, device="cpu", compute_type="int8", download_root=str(model_root))


def ensure_speechbrain(root: Path) -> None:
    from speechbrain.inference.speaker import EncoderClassifier

    model_root = root / "speechbrain-ecapa"
    model_root.mkdir(parents=True, exist_ok=True)
    EncoderClassifier.from_hparams(
        source=os.getenv("FORENSICS_SPEECHBRAIN_SOURCE", "speechbrain/spkrec-ecapa-voxceleb"),
        savedir=str(model_root),
        run_opts={"device": "cpu"},
    )


def main() -> None:
    root = Path(os.getenv("FORENSICS_MODEL_ROOT", "/app/model_assets"))
    root.mkdir(parents=True, exist_ok=True)
    ensure_fasttext(root)
    ensure_whisper(root)
    ensure_speechbrain(root)


if __name__ == "__main__":
    main()
