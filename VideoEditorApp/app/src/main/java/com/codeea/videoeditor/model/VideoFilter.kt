package com.codeea.videoeditor.model

enum class VideoFilter(
    val displayName: String,
    val brightness: Float = 0f,
    val contrast: Float = 1f,
    val saturation: Float = 1f,
    val colorTintR: Float = 1f,
    val colorTintG: Float = 1f,
    val colorTintB: Float = 1f
) {
    NONE("Original"),
    VIVID("Vivid",          brightness = 0.1f,  contrast = 1.2f,  saturation = 1.5f),
    COOL("Cool",            brightness = 0f,    contrast = 1.1f,  saturation = 0.9f,  colorTintR = 0.9f, colorTintB = 1.15f),
    WARM("Warm",            brightness = 0.05f, contrast = 1.1f,  saturation = 1.1f,  colorTintR = 1.15f, colorTintB = 0.9f),
    NOIR("Noir",            brightness = -0.05f, contrast = 1.3f, saturation = 0f),
    FADE("Fade",            brightness = 0.15f, contrast = 0.85f, saturation = 0.7f),
    VINTAGE("Vintage",      brightness = -0.05f, contrast = 1.1f, saturation = 0.8f,  colorTintR = 1.1f, colorTintG = 0.95f, colorTintB = 0.85f),
    DRAMA("Drama",          brightness = -0.1f, contrast = 1.4f,  saturation = 1.3f),
    CINEMATIC("Cinematic",  brightness = -0.05f, contrast = 1.2f, saturation = 0.85f, colorTintR = 1.05f, colorTintB = 1.1f);
}
