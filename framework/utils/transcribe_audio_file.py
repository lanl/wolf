import sys
from pathlib import Path
from faster_whisper import WhisperModel


def transcribe(audio_path: str, model_size="base"):
    audio_path = Path(audio_path)

    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    print(f"[+] Loading Whisper model: {model_size}")

    model = WhisperModel(
        model_size,
        device="cpu",        # change to "cuda" if GPU available
        compute_type="int8"
    )

    print(f"[+] Transcribing: {audio_path}")

    segments, info = model.transcribe(
        str(audio_path),
        beam_size=5,
    )

    transcript_lines = []

    print("\n--- TRANSCRIPT ---\n")

    for seg in segments:
        line = f"[{seg.start:6.2f} → {seg.end:6.2f}] {seg.text.strip()}"
        transcript_lines.append(line)
        print(line)

    transcript_file = audio_path.with_suffix(".txt")

    with open(transcript_file, "w") as f:
        f.write("\n".join(transcript_lines))

    print(f"\n[+] Transcript saved to: {transcript_file}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python transcribe_audio.py <audio_file>")
        sys.exit(1)

    transcribe(sys.argv[1])
