package com.finday.finday_mobile

import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import android.os.Bundle
import io.flutter.embedding.android.FlutterActivity

class MainActivity : FlutterActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        createRequestsChannel()
    }

    /**
     * Customer-request alerts use their own high-importance channel, so they pop up with a sound.
     * The id must match what the server sends (apps/quote_requests/push.py: CHANNEL_ID) and the
     * default channel in AndroidManifest.xml.
     */
    private fun createRequestsChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(
            "customer_requests",
            "Customer requests",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "New and updated requests from your customers"
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }
}
