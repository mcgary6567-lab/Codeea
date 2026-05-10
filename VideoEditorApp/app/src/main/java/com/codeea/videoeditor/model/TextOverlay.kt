package com.codeea.videoeditor.model

import android.graphics.Color
import java.io.Serializable

data class TextOverlay(
    val id: String,
    var text: String,
    var textSizeSp: Float = 28f,
    var textColor: Int = Color.WHITE,
    var fontStyle: FontStyle = FontStyle.NORMAL,
    var positionX: Float = 0.5f,
    var positionY: Float = 0.5f,
    var startTimeMs: Long = 0L,
    var endTimeMs: Long = 3000L,
    var backgroundAlpha: Int = 0,
    var rotation: Float = 0f
) : Serializable {
    enum class FontStyle {
        NORMAL, BOLD, ITALIC, BOLD_ITALIC, SANS_SERIF, SERIF, MONOSPACE
    }
}
