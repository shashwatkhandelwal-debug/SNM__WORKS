package com.snmworks.mobile

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.jan.supabase.auth.auth
import kotlinx.coroutines.launch

@Composable
fun DowntimeFormScreen(
    onBack: () -> Unit,
    onSubmitSuccess: (DowntimeLogResponse) -> Unit
) {
    val scope = rememberCoroutineScope()
    var isLoadingOptions by remember { mutableStateOf(true) }
    var isSubmitting by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf<String?>(null) }

    var machines by remember { mutableStateOf<List<String>>(emptyList()) }
    var shifts by remember { mutableStateOf<List<String>>(emptyList()) }
    var reasons by remember { mutableStateOf<List<String>>(emptyList()) }
    var jobs by remember { mutableStateOf<List<DowntimeJobOption>>(emptyList()) }

    var selectedMachine by remember { mutableStateOf("") }
    var machineDropdownExpanded by remember { mutableStateOf(false) }

    var selectedShift by remember { mutableStateOf("Shift A (06:00-14:00)") }
    var shiftDropdownExpanded by remember { mutableStateOf(false) }

    var selectedReason by remember { mutableStateOf("Mechanical Breakdown") }
    var reasonDropdownExpanded by remember { mutableStateOf(false) }

    var selectedJob by remember { mutableStateOf<DowntimeJobOption?>(null) }
    var jobDropdownExpanded by remember { mutableStateOf(false) }

    var minutes by remember { mutableStateOf("45.0") }
    var remarks by remember { mutableStateOf("") }

    fun loadOptions() {
        isLoadingOptions = true
        errorMessage = null
        scope.launch {
            val token = SupabaseClient.client.auth.currentAccessTokenOrNull()
            if (token == null) {
                errorMessage = "Authentication token expired. Please sign in again."
                isLoadingOptions = false
                return@launch
            }
            val result = DowntimeApi.fetchOptions(token)
            result.onSuccess { options ->
                machines = options.machines
                shifts = options.shifts
                reasons = options.reasons
                jobs = options.jobs

                if (options.machines.isNotEmpty() && selectedMachine.isEmpty()) {
                    selectedMachine = options.machines.first()
                }
                if (options.shifts.isNotEmpty()) {
                    selectedShift = options.shifts.first()
                }
                if (options.reasons.isNotEmpty()) {
                    selectedReason = options.reasons.first()
                }
                isLoadingOptions = false
            }.onFailure { err ->
                errorMessage = err.message ?: "Failed to load downtime options"
                isLoadingOptions = false
            }
        }
    }

    LaunchedEffect(Unit) {
        loadOptions()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp)
    ) {
        // Header
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "LOG DOWNTIME",
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                    color = SnmDark,
                    letterSpacing = 1.sp
                )
                Text(
                    text = "Shop-Floor Machine Stoppage",
                    fontSize = 13.sp,
                    color = Color.Gray
                )
            }
            OutlinedButton(
                onClick = onBack,
                shape = RoundedCornerShape(8.dp),
                modifier = Modifier.height(38.dp)
            ) {
                Text("Cancel", fontSize = 13.sp, color = SnmDark)
            }
        }

        Spacer(modifier = Modifier.height(16.dp))

        if (isLoadingOptions) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(200.dp),
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    CircularProgressIndicator(color = SnmOlive)
                    Spacer(modifier = Modifier.height(12.dp))
                    Text("Loading machines & reasons...", fontSize = 14.sp, color = Color.Gray)
                }
            }
            return
        }

        if (errorMessage != null) {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = Color(0xFFFFEBEE)),
                shape = RoundedCornerShape(8.dp)
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text(
                        text = errorMessage ?: "",
                        color = SnmFail,
                        fontSize = 13.sp
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Button(
                        onClick = { loadOptions() },
                        colors = ButtonDefaults.buttonColors(containerColor = SnmFail),
                        shape = RoundedCornerShape(6.dp),
                        modifier = Modifier.height(36.dp)
                    ) {
                        Text("Retry", fontSize = 12.sp)
                    }
                }
            }
            Spacer(modifier = Modifier.height(16.dp))
        }

        // Machine Selector
        Text("Machine / Loom", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
        Spacer(modifier = Modifier.height(4.dp))
        Box(modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.White, RoundedCornerShape(8.dp))
                    .border(1.dp, Color.LightGray, RoundedCornerShape(8.dp))
                    .clickable { machineDropdownExpanded = true }
                    .padding(horizontal = 14.dp, vertical = 14.dp)
            ) {
                Text(
                    text = if (selectedMachine.isNotEmpty()) selectedMachine else "Select Machine",
                    fontSize = 14.sp,
                    color = if (selectedMachine.isNotEmpty()) SnmDark else Color.Gray,
                    fontWeight = FontWeight.Medium
                )
            }
            DropdownMenu(
                expanded = machineDropdownExpanded,
                onDismissRequest = { machineDropdownExpanded = false }
            ) {
                machines.forEach { m ->
                    DropdownMenuItem(
                        text = { Text(m, fontWeight = FontWeight.Medium) },
                        onClick = {
                            selectedMachine = m
                            machineDropdownExpanded = false
                        }
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Shift Selector
        Text("Shift", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
        Spacer(modifier = Modifier.height(4.dp))
        Box(modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.White, RoundedCornerShape(8.dp))
                    .border(1.dp, Color.LightGray, RoundedCornerShape(8.dp))
                    .clickable { shiftDropdownExpanded = true }
                    .padding(horizontal = 14.dp, vertical = 14.dp)
            ) {
                Text(
                    text = selectedShift,
                    fontSize = 14.sp,
                    color = SnmDark
                )
            }
            DropdownMenu(
                expanded = shiftDropdownExpanded,
                onDismissRequest = { shiftDropdownExpanded = false }
            ) {
                shifts.forEach { s ->
                    DropdownMenuItem(
                        text = { Text(s) },
                        onClick = {
                            selectedShift = s
                            shiftDropdownExpanded = false
                        }
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Reason Selector
        Text("Stoppage Reason", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
        Spacer(modifier = Modifier.height(4.dp))
        Box(modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.White, RoundedCornerShape(8.dp))
                    .border(1.dp, Color.LightGray, RoundedCornerShape(8.dp))
                    .clickable { reasonDropdownExpanded = true }
                    .padding(horizontal = 14.dp, vertical = 14.dp)
            ) {
                Text(
                    text = selectedReason,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Medium,
                    color = SnmDark
                )
            }
            DropdownMenu(
                expanded = reasonDropdownExpanded,
                onDismissRequest = { reasonDropdownExpanded = false }
            ) {
                reasons.forEach { r ->
                    DropdownMenuItem(
                        text = { Text(r) },
                        onClick = {
                            selectedReason = r
                            reasonDropdownExpanded = false
                        }
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Job Selector (Optional)
        Text("Production Job (Optional)", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
        Spacer(modifier = Modifier.height(4.dp))
        Box(modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.White, RoundedCornerShape(8.dp))
                    .border(1.dp, Color.LightGray, RoundedCornerShape(8.dp))
                    .clickable { jobDropdownExpanded = true }
                    .padding(horizontal = 14.dp, vertical = 14.dp)
            ) {
                Text(
                    text = if (selectedJob != null) {
                        "${selectedJob?.jobNo} — ${selectedJob?.product}"
                    } else {
                        "Select Active Job (Optional)"
                    },
                    fontSize = 14.sp,
                    color = if (selectedJob != null) SnmDark else Color.Gray,
                    fontFamily = if (selectedJob != null) FontFamily.Monospace else FontFamily.Default
                )
            }
            DropdownMenu(
                expanded = jobDropdownExpanded,
                onDismissRequest = { jobDropdownExpanded = false }
            ) {
                DropdownMenuItem(
                    text = { Text("None (General Stoppage)", color = Color.Gray) },
                    onClick = {
                        selectedJob = null
                        jobDropdownExpanded = false
                    }
                )
                jobs.forEach { job ->
                    DropdownMenuItem(
                        text = {
                            Column {
                                Text(job.jobNo, fontWeight = FontWeight.Bold, fontFamily = FontFamily.Monospace)
                                Text(job.product, fontSize = 12.sp, color = Color.Gray)
                            }
                        },
                        onClick = {
                            selectedJob = job
                            jobDropdownExpanded = false
                        }
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Minutes Input
        Text("Stoppage Duration (Minutes)", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
        Spacer(modifier = Modifier.height(4.dp))
        OutlinedTextField(
            value = minutes,
            onValueChange = { minutes = it },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            enabled = !isSubmitting
        )

        Spacer(modifier = Modifier.height(14.dp))

        // Remarks Input
        Text("Operator Remarks", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
        Spacer(modifier = Modifier.height(4.dp))
        OutlinedTextField(
            value = remarks,
            onValueChange = { remarks = it },
            placeholder = { Text("e.g. Drive motor belt slipped") },
            singleLine = false,
            modifier = Modifier.fillMaxWidth(),
            enabled = !isSubmitting
        )

        Spacer(modifier = Modifier.height(24.dp))

        // Submit Button
        Button(
            onClick = {
                val mins = minutes.toDoubleOrNull()
                if (selectedMachine.isBlank()) {
                    errorMessage = "Please select a machine"
                    return@Button
                }
                if (mins == null || mins < 0) {
                    errorMessage = "Please enter valid non-negative minutes"
                    return@Button
                }

                isSubmitting = true
                errorMessage = null

                scope.launch {
                    val token = SupabaseClient.client.auth.currentAccessTokenOrNull()
                    if (token == null) {
                        errorMessage = "Session expired. Please sign in again."
                        isSubmitting = false
                        return@launch
                    }

                    val req = DowntimeLogRequest(
                        machine = selectedMachine,
                        shift = selectedShift,
                        reason = selectedReason,
                        minutes = mins,
                        jobId = selectedJob?.id,
                        remarks = remarks.trim().ifEmpty { null }
                    )

                    val result = DowntimeApi.submitLog(req, token)
                    result.onSuccess { resp ->
                        isSubmitting = false
                        onSubmitSuccess(resp)
                    }.onFailure { err ->
                        isSubmitting = false
                        errorMessage = err.message ?: "Failed to log downtime"
                    }
                }
            },
            modifier = Modifier
                .fillMaxWidth()
                .height(50.dp),
            shape = RoundedCornerShape(8.dp),
            colors = ButtonDefaults.buttonColors(containerColor = SnmOlive),
            enabled = !isSubmitting
        ) {
            if (isSubmitting) {
                CircularProgressIndicator(
                    color = Color.White,
                    modifier = Modifier.size(24.dp),
                    strokeWidth = 2.dp
                )
            } else {
                Text(
                    text = "Log Stoppage",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold
                )
            }
        }

        Spacer(modifier = Modifier.height(24.dp))
    }
}

@Composable
fun DowntimeResultScreen(
    result: DowntimeLogResponse,
    onNewLog: () -> Unit,
    onDone: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(containerColor = SnmGreige)
        ) {
            Column(
                modifier = Modifier.padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    text = "DOWNTIME LOGGED",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color.Gray,
                    letterSpacing = 1.sp
                )

                Spacer(modifier = Modifier.height(12.dp))

                Text(
                    text = result.logNo,
                    fontSize = 32.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = SnmDark
                )

                Spacer(modifier = Modifier.height(16.dp))

                Box(
                    modifier = Modifier
                        .background(Color(0xFFFFF3E0), RoundedCornerShape(8.dp))
                        .border(2.dp, Color(0xFFE65100), RoundedCornerShape(8.dp))
                        .padding(horizontal = 24.dp, vertical = 8.dp)
                ) {
                    Text(
                        text = result.formattedDuration,
                        fontSize = 24.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        color = Color(0xFFE65100),
                        letterSpacing = 1.sp
                    )
                }

                Spacer(modifier = Modifier.height(20.dp))

                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color.White, RoundedCornerShape(8.dp))
                        .padding(14.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("Machine:", fontSize = 13.sp, color = Color.Gray)
                        Text(
                            result.machine,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = SnmDark
                        )
                    }
                    Spacer(modifier = Modifier.height(6.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("Shift:", fontSize = 13.sp, color = Color.Gray)
                        Text(
                            result.shift,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Medium,
                            color = SnmDark
                        )
                    }
                    Spacer(modifier = Modifier.height(6.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text("Reason:", fontSize = 13.sp, color = Color.Gray)
                        Text(
                            result.reason,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = SnmDark
                        )
                    }
                    if (!result.remarks.isNullOrBlank()) {
                        Spacer(modifier = Modifier.height(6.dp))
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween
                        ) {
                            Text("Remarks:", fontSize = 13.sp, color = Color.Gray)
                            Text(
                                result.remarks ?: "",
                                fontSize = 13.sp,
                                color = SnmDark
                            )
                        }
                    }
                }
            }
        }

        Spacer(modifier = Modifier.height(28.dp))

        Button(
            onClick = onNewLog,
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp),
            shape = RoundedCornerShape(8.dp),
            colors = ButtonDefaults.buttonColors(containerColor = SnmOlive)
        ) {
            Text("Log Another Stoppage", fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        }

        Spacer(modifier = Modifier.height(12.dp))

        OutlinedButton(
            onClick = onDone,
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp),
            shape = RoundedCornerShape(8.dp)
        ) {
            Text("Back to Dashboard", fontSize = 15.sp, color = SnmDark)
        }
    }
}
