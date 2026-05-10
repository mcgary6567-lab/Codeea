package com.codeea.videoeditor.model

import android.net.Uri
import java.io.Serializable

data class AudioTrack(
    val id: String,
    val uri: Uri,
    val name: String,
    val artist: String = "Unknown",
    val durationMs: Long,
    var startOffsetMs: Long = 0L,
    var trimStartMs: Long = 0L,
    var trimEndMs: Long = durationMs,
    var volume: Float = 0.8f,
    var isMuted: Boolean = false
) : Serializable {
    val displayName: String
        get() = if (artist.isNotEmpty() && artist != "Unknown") "$name - $artist" else name
}
