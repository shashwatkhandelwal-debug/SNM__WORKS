package com.snmworks.mobile

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.jan.supabase.auth.auth
import kotlinx.coroutines.launch

@Composable
fun DowntimeHistoryScreen(
    onBack: () -> Unit,
    onLogNew: () -> Unit = {}
) {
    val scope = rememberCoroutineScope()
    var logs by remember { mutableStateOf<List<DowntimeLogListItem>>(emptyList()) }
    var isLoading by remember { mutableStateOf(true) }
    var errorMessage by remember { mutableStateOf<String?>(null) }

    fun loadLogs() {
        isLoading = true
        errorMessage = null
        scope.launch {
            val token = SupabaseClient.client.auth.currentAccessTokenOrNull()
            if (token == null) {
                errorMessage = "Authentication token expired. Please sign in again."
                isLoading = false
                return@launch
            }
            val result = DowntimeApi.fetchRecentLogs(token, limit = 50)
            result.onSuccess { fetchedLogs ->
                logs = fetchedLogs
                isLoading = false
            }.onFailure { err ->
                errorMessage = err.message ?: "Failed to load downtime history"
                isLoading = false
            }
        }
    }

    LaunchedEffect(Unit) {
        loadLogs()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        // Top bar
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            OutlinedButton(
                onClick = onBack,
                shape = RoundedCornerShape(8.dp)
            ) {
                Text("← Back", color = SnmDark)
            }
            Text(
                text = "Downtime History",
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                color = SnmDark
            )
            Button(
                onClick = { loadLogs() },
                colors = ButtonDefaults.buttonColors(containerColor = SnmDark),
                shape = RoundedCornerShape(8.dp)
            ) {
                Text("Refresh", fontSize = 13.sp)
            }
        }

        Spacer(modifier = Modifier.height(16.dp))

        when {
            isLoading -> {
                Box(
                    modifier = Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = SnmOlive)
                }
            }
            errorMessage != null -> {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(8.dp),
                    colors = CardDefaults.cardColors(containerColor = SnmPaper)
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Text(
                            text = "Error Loading History",
                            color = SnmFail,
                            fontWeight = FontWeight.Bold
                        )
                        Spacer(modifier = Modifier.height(4.dp))
                        Text(
                            text = errorMessage ?: "",
                            color = SnmDark,
                            fontSize = 14.sp
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        Button(
                            onClick = { loadLogs() },
                            colors = ButtonDefaults.buttonColors(containerColor = SnmOlive)
                        ) {
                            Text("Retry")
                        }
                    }
                }
            }
            logs.isEmpty() -> {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(8.dp),
                    colors = CardDefaults.cardColors(containerColor = SnmPaper)
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(24.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text(
                            text = "No downtime logs recorded yet",
                            color = SnmDark,
                            fontSize = 15.sp
                        )
                    }
                }
            }
            else -> {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    items(logs) { item ->
                        DowntimeHistoryCard(item)
                    }
                }
            }
        }
    }
}

@Composable
fun DowntimeHistoryCard(item: DowntimeLogListItem) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = SnmPaper),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = item.reason,
                    fontWeight = FontWeight.Bold,
                    fontSize = 15.sp,
                    color = SnmDark
                )
                Text(
                    text = item.formattedDuration,
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                    fontSize = 13.sp,
                    color = SnmOlive
                )
            }

            Spacer(modifier = Modifier.height(4.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = "Machine: ${item.machine}",
                    fontSize = 13.sp,
                    color = SnmDark
                )
                Text(
                    text = "Shift: ${item.shift}",
                    fontSize = 12.sp,
                    color = SnmDark
                )
            }

            if (!item.jobNo.isNullOrBlank()) {
                Spacer(modifier = Modifier.height(2.dp))
                Text(
                    text = "Job: ${item.jobNo}",
                    fontSize = 12.sp,
                    color = SnmDark
                )
            }

            if (!item.remarks.isNullOrBlank()) {
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = item.remarks,
                    fontSize = 12.sp,
                    color = Color.DarkGray
                )
            }

            Spacer(modifier = Modifier.height(4.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = item.logNo,
                    fontFamily = FontFamily.Monospace,
                    fontSize = 11.sp,
                    color = Color.Gray
                )
                Text(
                    text = item.loggedOn ?: "",
                    fontFamily = FontFamily.Monospace,
                    fontSize = 11.sp,
                    color = Color.Gray
                )
            }
        }
    }
}
