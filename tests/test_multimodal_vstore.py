import asyncio
from pathlib import Path

from framework.data_store.multimodal_vstore import MultiModalVectorStore
from framework.utils.config_tools import create_session_dir


def has_modality(results, modality: str) -> bool:
    return any(r.get("modality") == modality for r in results)


def has_source(results, suffix: str) -> bool:
    return any(str(r.get("source", "")).endswith(suffix) for r in results)


async def main():
    session_dir = create_session_dir()
    params = {
        "persist_directory": f"{session_dir}/vstore_db",
        "collection_name": "multimodal_vs_test",
        "rebuild_vstore": False,
        "use_bm25": True,
        "use_rrf": True,
        "rrf_k": 60,
        "use_reranker": False,
        "allow_online": False,
    }

    vstore = MultiModalVectorStore(params)

    assets = {
        "pdf": Path("tests/data/docs/JaxFluid2.0_Paper.pdf"),
        "image": Path("tests/data/images/Image1.png"),
        "audio": Path("tests/data/audios/Audio2.wav"),
        "video": Path("tests/data/videos/Video2.mov"),
        "binary": Path("tests/data/bin/Binary1.bin"),
    }

    existing_docs = [str(p) for p in assets.values() if p.exists()]

    print("[+][TEST][MULTIMODAL-VS]: Existing assets:")
    for name, path in assets.items():
        print(f"    - {name}: {'FOUND' if path.exists() else 'MISSING'} -> {path}")

    if not existing_docs:
        raise RuntimeError("No test assets found.")

    await vstore.add_documents(existing_docs)

    print("\n[+][TEST][MULTIMODAL-VS]: Documents Added OK")
    stats = vstore.get_stats()
    print("[+][TEST][MULTIMODAL-VS][STATS]:")
    print(stats)

    expected_sources = sum(1 for p in assets.values() if p.exists())
    assert stats["unique_sources"] == expected_sources, (
        f"Expected {expected_sources} sources, got {stats['unique_sources']}"
    )

    if assets["pdf"].exists():
        assert stats["modalities"].get("text", 0) > 0, "Expected text chunks from PDF"

    if assets["image"].exists():
        assert stats["modalities"].get("image", 0) > 0, "Expected image entries"

    if assets["audio"].exists():
        assert stats["modalities"].get("audio", 0) > 0, "Expected audio entries"

    if assets["video"].exists():
        assert stats["modalities"].get("video_frame", 0) > 0, "Expected video_frame entries"

    if assets["binary"].exists():
        assert stats["modalities"].get("binary", 0) > 0, "Expected binary entries"

    # --------------------------------------------------
    # TEXT QUERY
    # --------------------------------------------------
    if assets["pdf"].exists():
        text_query = "finite volume discretization cartesian grid cut cell"
        text_results = await vstore.query_hybrid(text_query, k=8)
    
        print("\n[+][TEST][TEXT QUERY RESULTS]:")
        for r in text_results:
            print(r["modality"], r["source"], r["document"][:120])
    
        # assertions go here
        assert has_modality(text_results, "text")
        assert has_source(text_results, "JaxFluid2.0_Paper.pdf")
    # --------------------------------------------------
    # IMAGE QUERY
    # --------------------------------------------------
    if assets["image"].exists():
        image_query = "terminal screenshot showing apps directory listing with anaconda3 wolf new_wolf ui.py"
        image_results = await vstore.query_hybrid(
            image_query,
            k=8,
            channel_weights={"dense": 0.8, "bm25": 0.5, "vision": 1.8},
        )
    
        print("\n[+][TEST][IMAGE QUERY RESULTS]:")
        for r in image_results:
            print(r["modality"], r["source"], r["document"][:120])
    
        # assertions here
        assert has_modality(image_results, "image")
        assert has_source(image_results, "Image1.png")

    # --------------------------------------------------
    # AUDIO QUERY
    # --------------------------------------------------
    if assets["audio"].exists():
        audio_query = "trying to get charge gpt to help me debug"
        audio_results = await vstore.query_hybrid(audio_query, k=8)
    
        print("\n[+][TEST][AUDIO QUERY RESULTS]:")
        for r in audio_results:
            print(r["modality"], r["source"], r["document"][:160])
    
        # assertions here
        assert has_modality(audio_results, "audio")
        assert has_source(audio_results, "Audio2.wav")


    # --------------------------------------------------
    # VIDEO QUERY
    # --------------------------------------------------
    if assets["video"].exists():
        video_query = "screen recording where I say I am using chat gpt to help me debug"
        video_results = await vstore.query_hybrid(
            video_query,
            k=10,
            channel_weights={"dense": 1.0, "bm25": 0.6, "vision": 1.5},
        )
    
        print("\n[+][TEST][VIDEO QUERY RESULTS]:")
        for r in video_results:
            print(r["modality"], r["source"], r["document"][:120])
    
        # assertions here
        assert (
            has_modality(video_results, "video_frame")
            or has_modality(video_results, "video_transcript")
        )
        assert has_source(video_results, "Video2.mov")

    # --------------------------------------------------
    # BINARY QUERY
    # --------------------------------------------------
    if assets["binary"].exists():
        binary_query = "binary file named Binary1.bin"
        binary_results = await vstore.query_hybrid(binary_query, k=8)
    
        print("\n[+][TEST][BINARY QUERY RESULTS]:")
        for r in binary_results:
            print(r["modality"], r["source"], r["document"][:120])
    
        # assertions here
        assert has_modality(binary_results, "binary")
        assert has_source(binary_results, "Binary1.bin")

    await vstore.close()
    print("\n[+][TEST][MULTIMODAL-VS]: PASS")


if __name__ == "__main__":
    asyncio.run(main())
