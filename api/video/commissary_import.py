"""Commissary: import any file or folder, not only a failed download.

Under /api/video/import/, which upstream's gate makes admin-only for every
method - browse enumerates folders on the host.

GET  /import/browse?path=&q=               one folder, filtered (core/commissary/manual_import)
POST /import/add {path}                    queue a file or season folder for placement
GET  /import/pack-preview?path=&season=&media_id=   what a folder would import, and as what
POST /import/<id>/place-folder             place every episode as the chosen show
GET  /import/<id>/place/status             where a slow placement got to
"""

from __future__ import annotations

import os

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.commissary_import")


def register_routes(bp):
    @bp.route("/import/browse", methods=["GET"])
    def commissary_import_browse():
        from . import get_video_db
        from core.commissary import manual_import
        out, code = manual_import.browse(get_video_db(), request.args.get("path"),
                                         request.args.get("q"))
        return jsonify(out), code

    @bp.route("/import/add", methods=["POST"])
    def commissary_import_add():
        from . import get_video_db
        from core.commissary import manual_import
        out, code = manual_import.add(get_video_db(), (request.get_json(silent=True) or {}).get("path"))
        return jsonify(out), code

    @bp.route("/import/pack-preview", methods=["GET"])
    def commissary_import_pack_preview():
        from . import get_video_db
        from core.commissary import manual_import
        path = str(request.args.get("path") or "").strip()
        if not path or not os.path.isdir(path):
            return jsonify({"success": False, "error": "Not a folder."}), 404
        return jsonify(manual_import.preview(get_video_db(), path, request.args.get("season"),
                                             request.args.get("media_id")))

    @bp.route("/import/<int:dl_id>/place-folder", methods=["POST"])
    def commissary_import_place_folder(dl_id):
        from . import get_video_db
        from core.commissary import manual_import
        db = get_video_db()
        if manual_import.is_running(dl_id):
            return jsonify({"success": True, "running": True, "id": dl_id})
        row = db.get_video_download(dl_id)
        if row and row.get("status") == "completed":
            # a retry after a dropped connection: it already landed
            return jsonify({"success": True, "status": "completed", "already": True,
                            "dest_path": row.get("dest_path")})
        if not row or row.get("status") != "import_failed":
            return jsonify({"success": False, "error": "Not an unplaced import."}), 404
        if not row.get("dest_path") or not os.path.isdir(row["dest_path"]):
            return jsonify({"success": False, "error": "That folder is no longer on disk."}), 410
        body = request.get_json(silent=True) or {}
        if not body.get("media_id"):
            return jsonify({"success": False, "error": "Choose the show first."}), 400
        snap = manual_import.start(dl_id, lambda: manual_import.place_folder(db, row, body))
        if snap.get("running"):
            return jsonify({"success": True, "running": True, "id": dl_id})
        if snap.get("error"):
            return jsonify({"success": False, "error": snap["error"]}), 500
        return jsonify(snap.get("result") or {"success": False, "error": "Couldn't place the folder."})

    @bp.route("/import/<int:dl_id>/place/status", methods=["GET"])
    def commissary_import_place_status(dl_id):
        """Answers from the job while it runs, then from the row - the durable
        record - so a restart or a reaped job never reads as a failure."""
        from . import get_video_db
        from core.commissary import manual_import
        snap = manual_import.job_state(dl_id)
        if snap and snap.get("running"):
            return jsonify({"success": True, "running": True, "id": dl_id})
        if snap and snap.get("error"):
            return jsonify({"success": False, "running": False, "error": snap["error"]})
        if snap and snap.get("result") is not None:
            return jsonify({"running": False, **snap["result"]})
        row = get_video_db().get_video_download(dl_id)
        if not row:
            return jsonify({"success": False, "running": False, "error": "Not an import."}), 404
        placed = row.get("status") == "completed"
        return jsonify({"success": placed, "running": False, "status": row.get("status"),
                        "dest_path": row.get("dest_path") if placed else None,
                        "error": None if placed else row.get("error")})
