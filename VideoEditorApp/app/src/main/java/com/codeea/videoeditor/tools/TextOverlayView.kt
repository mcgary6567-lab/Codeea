package com.codeea.videoeditor.tools

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.util.AttributeSet
import android.util.TypedValue
import android.view.MotionEvent
import android.view.View
import com.codeea.videoeditor.model.TextOverlay

class TextOverlayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    interface OverlayInteractionListener {
        fun onOverlaySelected(overlay: TextOverlay)
        fun onOverlayMoved(overlay: TextOverlay)
    }

    var interactionListener: OverlayInteractionListener? = null

    private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG)

    private var overlays: List<TextOverlay> = emptyList()
    private var currentPositionMs: Long = 0L
    private var selectedOverlayId: String? = null

    private var dragOverlay: TextOverlay? = null
    private var dragStartX = 0f
    private var dragStartY = 0f
    private var overlayStartPosX = 0f
    private var overlayStartPosY = 0f

    init {
        setWillNotDraw(false)
    }

    fun setOverlays(overlays: List<TextOverlay>) {
        this.overlays = overlays
        invalidate()
    }

    fun setCurrentPosition(positionMs: Long) {
        currentPositionMs = positionMs
        invalidate()
    }

    fun setSelectedOverlay(id: String?) {
        selectedOverlayId = id
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        overlays.filter { it.startTimeMs <= currentPositionMs && it.endTimeMs >= currentPositionMs }
            .forEach { overlay ->
                drawOverlay(canvas, overlay)
            }
    }

    private fun drawOverlay(canvas: Canvas, overlay: TextOverlay) {
        val x = overlay.positionX * width
        val y = overlay.positionY * height

        val textSizePx = TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_SP, overlay.textSizeSp, resources.displayMetrics
        )

        textPaint.textSize = textSizePx
        textPaint.color = overlay.textColor
        textPaint.typeface = when (overlay.fontStyle) {
            TextOverlay.FontStyle.BOLD -> Typeface.DEFAULT_BOLD
            TextOverlay.FontStyle.ITALIC -> Typeface.create(Typeface.DEFAULT, Typeface.ITALIC)
            TextOverlay.FontStyle.BOLD_ITALIC -> Typeface.create(Typeface.DEFAULT, Typeface.BOLD_ITALIC)
            TextOverlay.FontStyle.SERIF -> Typeface.SERIF
            TextOverlay.FontStyle.MONOSPACE -> Typeface.MONOSPACE
            else -> Typeface.DEFAULT
        }
        textPaint.textAlign = Paint.Align.CENTER

        if (overlay.backgroundAlpha > 0) {
            bgPaint.color = Color.argb(overlay.backgroundAlpha, 0, 0, 0)
            val padding = textSizePx * 0.3f
            val textWidth = textPaint.measureText(overlay.text)
            canvas.drawRoundRect(
                x - textWidth / 2 - padding,
                y - textSizePx - padding,
                x + textWidth / 2 + padding,
                y + padding,
                8f, 8f, bgPaint
            )
        }

        canvas.save()
        canvas.rotate(overlay.rotation, x, y)

        // Shadow
        textPaint.setShadowLayer(4f, 2f, 2f, Color.argb(128, 0, 0, 0))
        canvas.drawText(overlay.text, x, y, textPaint)
        textPaint.clearShadowLayer()

        // Selection indicator
        if (overlay.id == selectedOverlayId) {
            val selPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = 0xFFFF6B35.toInt()
                style = Paint.Style.STROKE
                strokeWidth = 3f
            }
            val textWidth = textPaint.measureText(overlay.text)
            val pad = textSizePx * 0.4f
            canvas.drawRoundRect(
                x - textWidth / 2 - pad,
                y - textSizePx - pad,
                x + textWidth / 2 + pad,
                y + pad,
                6f, 6f, selPaint
            )
        }

        canvas.restore()
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                val touchX = event.x / width
                val touchY = event.y / height
                val tapped = findOverlayAt(touchX, touchY)
                if (tapped != null) {
                    dragOverlay = tapped
                    dragStartX = event.x
                    dragStartY = event.y
                    overlayStartPosX = tapped.positionX
                    overlayStartPosY = tapped.positionY
                    selectedOverlayId = tapped.id
                    interactionListener?.onOverlaySelected(tapped)
                    invalidate()
                    return true
                }
            }
            MotionEvent.ACTION_MOVE -> {
                dragOverlay?.let { overlay ->
                    val dx = (event.x - dragStartX) / width
                    val dy = (event.y - dragStartY) / height
                    overlay.positionX = (overlayStartPosX + dx).coerceIn(0f, 1f)
                    overlay.positionY = (overlayStartPosY + dy).coerceIn(0f, 1f)
                    interactionListener?.onOverlayMoved(overlay)
                    invalidate()
                    return true
                }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                dragOverlay = null
            }
        }
        return false
    }

    private fun findOverlayAt(normX: Float, normY: Float): TextOverlay? {
        val touchRadiusNorm = 0.15f
        return overlays
            .filter { it.startTimeMs <= currentPositionMs && it.endTimeMs >= currentPositionMs }
            .firstOrNull { overlay ->
                val dx = Math.abs(overlay.positionX - normX)
                val dy = Math.abs(overlay.positionY - normY)
                dx < touchRadiusNorm && dy < touchRadiusNorm * 2
            }
    }
}
