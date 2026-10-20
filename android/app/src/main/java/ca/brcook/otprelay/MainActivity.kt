// SPDX-License-Identifier: AGPL-3.0-or-later
package ca.brcook.otprelay

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.CoroutineScope

class MainActivity : ComponentActivity() {
    private lateinit var state: AppState

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        state = AppState(applicationContext)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) { App(state) }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // Notification access is granted in a system screen, and the listener
        // records what it has sent, so both are re-read on every return here.
        state.refreshFromSystem(this)
    }
}

@Composable
private fun App(state: AppState) {
    val scope = rememberCoroutineScope()

    state.message?.let { text ->
        AlertDialog(
            onDismissRequest = state::dismissMessage,
            confirmButton = { TextButton(onClick = state::dismissMessage) { Text("OK") } },
            text = { Text(text) },
        )
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("otprelay", style = MaterialTheme.typography.headlineMedium)
        if (state.registered) HomeScreen(state, scope) else SetupScreen(state, scope)
        if (state.busy) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                CircularProgressIndicator()
            }
        }
    }
}

@Composable
private fun SetupScreen(state: AppState, scope: CoroutineScope) {
    var relay by remember { mutableStateOf("https://") }
    var invite by remember { mutableStateOf("") }
    var name by remember { mutableStateOf(android.os.Build.MODEL ?: "Phone") }

    Text(
        "Connect this phone to your relay. Ask whoever runs it for an invite code.",
        style = MaterialTheme.typography.bodyMedium,
    )
    OutlinedTextField(
        value = relay,
        onValueChange = { relay = it },
        label = { Text("Relay address") },
        placeholder = { Text("https://otp.example.com") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    OutlinedTextField(
        value = invite,
        onValueChange = { invite = it },
        label = { Text("Invite code") },
        placeholder = { Text("a1b2-c3d4-e5f6-a7b8") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    OutlinedTextField(
        value = name,
        onValueChange = { name = it.take(64) },
        label = { Text("Name for this phone") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    Button(
        onClick = { state.register(scope, relay.trim(), invite.trim(), name.trim()) },
        enabled = !state.busy && relay.length > 8 && invite.isNotBlank(),
        modifier = Modifier.fillMaxWidth(),
    ) { Text("Register") }
}

@Composable
private fun HomeScreen(state: AppState, scope: CoroutineScope) {
    val context = LocalContext.current
    var confirmReset by remember { mutableStateOf(false) }
    var confirmUnpair by remember { mutableStateOf<ReceiverRow?>(null) }

    LaunchedEffect(state.resumeTick) { state.refresh(scope) }

    val scanner = rememberLauncherForActivityResult(ScanContract()) { result ->
        result.contents?.let { state.pair(scope, it) }
    }

    if (!state.notificationAccess) {
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text("Notification access is off", style = MaterialTheme.typography.titleSmall)
                Text(
                    "otprelay reads notifications to find codes. Until you allow that, " +
                        "nothing is forwarded. It looks at every app except the ones you " +
                        "exclude, and only the code itself ever leaves this phone.",
                    style = MaterialTheme.typography.bodySmall,
                )
                Button(
                    onClick = { context.startActivity(NotificationListener.settingsIntent()) },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Open notification access settings") }
            }
        }
    }

    Card(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(state.relayUrl, style = MaterialTheme.typography.bodyMedium)
            Text("This phone: ${state.sourceName}",
                style = MaterialTheme.typography.bodySmall)
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text("Forward codes")
                Switch(checked = state.forwarding, onCheckedChange = state::updateForwarding)
            }
            if (!state.forwarding) {
                Text(
                    "Forwarding is paused. Codes stay on this phone.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            if (state.lastEvent.isNotEmpty()) {
                Text(state.lastEvent, style = MaterialTheme.typography.bodySmall)
            }
        }
    }

    Text("Computers", style = MaterialTheme.typography.titleMedium)
    if (state.receivers.isEmpty()) {
        Text(
            "No computers yet. On your computer, open otprelay and choose " +
                "\"Add a phone\", then scan the code it shows.",
            style = MaterialTheme.typography.bodyMedium,
        )
    }
    for (row in state.receivers) {
        ReceiverCard(row) { confirmUnpair = row }
    }

    Button(
        onClick = {
            scanner.launch(
                ScanOptions()
                    .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                    .setPrompt("Scan the code shown on your computer")
                    .setBeepEnabled(false)
                    .setOrientationLocked(false)
            )
        },
        enabled = !state.busy,
        modifier = Modifier.fillMaxWidth(),
    ) { Text("Add a computer") }

    OutlinedButton(
        onClick = { state.refresh(scope) },
        enabled = !state.busy,
        modifier = Modifier.fillMaxWidth(),
    ) { Text("Refresh") }

    Spacer(modifier = Modifier.height(8.dp))
    TextButton(onClick = { confirmReset = true }, modifier = Modifier.fillMaxWidth()) {
        Text("Forget this relay", color = MaterialTheme.colorScheme.error)
    }

    confirmUnpair?.let { row ->
        AlertDialog(
            onDismissRequest = { confirmUnpair = null },
            title = { Text("Unpair ${row.name}?") },
            text = { Text("That computer will stop receiving codes from this phone.") },
            confirmButton = {
                TextButton(onClick = {
                    state.unpair(scope, row)
                    confirmUnpair = null
                }) { Text("Unpair") }
            },
            dismissButton = {
                TextButton(onClick = { confirmUnpair = null }) { Text("Cancel") }
            },
        )
    }

    if (confirmReset) {
        AlertDialog(
            onDismissRequest = { confirmReset = false },
            title = { Text("Forget this relay?") },
            text = {
                Text(
                    "This phone will stop sending codes and forget every paired computer. " +
                        "You will need a new invite code to set it up again."
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    state.reset()
                    confirmReset = false
                }) { Text("Forget") }
            },
            dismissButton = {
                TextButton(onClick = { confirmReset = false }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun ReceiverCard(row: ReceiverRow, onUnpair: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(row.name, style = MaterialTheme.typography.titleSmall)
                val status = when {
                    !row.hasKey -> "Paired with another phone; re-pair to use it here"
                    !row.claimed -> "Waiting for that computer to finish pairing"
                    row.online -> "Online"
                    else -> "Offline"
                }
                Text(
                    "${row.platform} · $status",
                    style = MaterialTheme.typography.bodySmall,
                    color = if (row.hasKey) MaterialTheme.colorScheme.onSurfaceVariant
                    else MaterialTheme.colorScheme.error,
                )
            }
            TextButton(onClick = onUnpair) { Text("Unpair") }
        }
    }
}
