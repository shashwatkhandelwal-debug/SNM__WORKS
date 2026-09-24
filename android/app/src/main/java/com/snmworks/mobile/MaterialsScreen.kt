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
fun MaterialsFormScreen(
    onBack: () -> Unit,
    onSubmitSuccess: (MaterialIssueResponse) -> Unit
) {
    val scope = rememberCoroutineScope()
    var isLoadingOptions by remember { mutableStateOf(true) }
    var isSubmitting by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf<String?>(null) }

    var jobs by remember { mutableStateOf<List<MaterialJobOption>>(emptyList()) }
    var approvedLots by remember { mutableStateOf<List<ApprovedYarnLotOption>>(emptyList()) }

    var selectedJob by remember { mutableStateOf<MaterialJobOption?>(null) }
    var jobDropdownExpanded by remember { mutableStateOf(false) }

    var selectedLot by remember { mutableStateOf<ApprovedYarnLotOption?>(null) }
    var lotDropdownExpanded by remember { mutableStateOf(false) }

    var qtyIssued by remember { mutableStateOf("25.0") }
    var unit by remember { mutableStateOf("kg") }
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
            val result = MaterialsApi.fetchOptions(token)
            result.onSuccess { options ->
                jobs = options.jobs
                approvedLots = options.approvedLots

                if (options.jobs.isNotEmpty() && selectedJob == null) {
                    selectedJob = options.jobs.first()
                }
                if (options.approvedLots.isNotEmpty() && selectedLot == null) {
                    selectedLot = options.approvedLots.first()
                    unit = options.approvedLots.first().unit
                }
                isLoadingOptions = false
            }.onFailure { err ->
                errorMessage = err.message ?: "Failed to load material options"
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
                    text = "ISSUE MATERIAL",
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                    color = SnmDark,
                    letterSpacing = 1.sp
                )
                Text(
                    text = "Yarn & Raw Material Issue",
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
                    Text("Loading approved yarn lots & jobs...", fontSize = 14.sp, color = Color.Gray)
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

        // Production Job Selector
        Text("Target Job Card", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
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
                        "Select Target Job"
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

        // Approved Yarn Lot Selector
        Text("Approved Yarn Lot (Stock)", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
        Spacer(modifier = Modifier.height(4.dp))
        Box(modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.White, RoundedCornerShape(8.dp))
                    .border(1.dp, Color.LightGray, RoundedCornerShape(8.dp))
                    .clickable { lotDropdownExpanded = true }
                    .padding(horizontal = 14.dp, vertical = 14.dp)
            ) {
                Column {
                    if (selectedLot != null) {
                        Text(
                            text = "${selectedLot?.lotNo} — ${selectedLot?.yarnType}",
                            fontSize = 14.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = SnmDark
                        )
                        Spacer(modifier = Modifier.height(2.dp))
                        Text(
                            text = "Available: ${selectedLot?.qtyRemaining} ${selectedLot?.unit}",
                            fontSize = 12.sp,
                            color = SnmPass,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold
                        )
                    } else {
                        Text(
                            text = "Select Approved Lot",
                            fontSize = 14.sp,
                            color = Color.Gray
                        )
                    }
                }
            }
            DropdownMenu(
                expanded = lotDropdownExpanded,
                onDismissRequest = { lotDropdownExpanded = false }
            ) {
                if (approvedLots.isEmpty()) {
                    DropdownMenuItem(
                        text = { Text("No Approved Yarn Lots with Stock Available", color = SnmFail) },
                        onClick = { lotDropdownExpanded = false }
                    )
                }
                approvedLots.forEach { lot ->
                    DropdownMenuItem(
                        text = {
                            Column {
                                Text(
                                    text = "${lot.lotNo} — ${lot.yarnType}",
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace
                                )
                                Text(
                                    text = "Available: ${lot.qtyRemaining} ${lot.unit} | Location: ${lot.storageLocation ?: "Warehouse"}",
                                    fontSize = 12.sp,
                                    color = SnmPass
                                )
                            }
                        },
                        onClick = {
                            selectedLot = lot
                            unit = lot.unit
                            lotDropdownExpanded = false
                        }
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Quantity & Unit Row
        Row(modifier = Modifier.fillMaxWidth()) {
            Column(modifier = Modifier.weight(0.65f)) {
                Text("Quantity to Issue", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
                Spacer(modifier = Modifier.height(4.dp))
                OutlinedTextField(
                    value = qtyIssued,
                    onValueChange = { qtyIssued = it },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !isSubmitting
                )
            }
            Spacer(modifier = Modifier.width(12.dp))
            Column(modifier = Modifier.weight(0.35f)) {
                Text("Unit", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
                Spacer(modifier = Modifier.height(4.dp))
                OutlinedTextField(
                    value = unit,
                    onValueChange = { unit = it },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !isSubmitting
                )
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        // Remarks Input
        Text("Issue Remarks / Destination", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = SnmDark)
        Spacer(modifier = Modifier.height(4.dp))
        OutlinedTextField(
            value = remarks,
            onValueChange = { remarks = it },
            placeholder = { Text("e.g. Issued to Loom NL-02 for Job Run") },
            singleLine = false,
            modifier = Modifier.fillMaxWidth(),
            enabled = !isSubmitting
        )

        Spacer(modifier = Modifier.height(24.dp))

        // Submit Button
        Button(
            onClick = {
                val qty = qtyIssued.toDoubleOrNull()
                if (selectedJob == null) {
                    errorMessage = "Please select a target job"
                    return@Button
                }
                if (selectedLot == null) {
                    errorMessage = "Please select an approved yarn lot"
                    return@Button
                }
                if (qty == null || qty <= 0) {
                    errorMessage = "Please enter a valid positive quantity to issue"
                    return@Button
                }

                val maxAvailable = selectedLot?.qtyRemaining ?: 0.0
                if (qty > maxAvailable) {
                    errorMessage = "Cannot issue $qty $unit (only $maxAvailable $unit available in lot)"
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

                    val req = MaterialIssueRequest(
                        jobId = selectedJob!!.id,
                        yarnLotId = selectedLot!!.id,
                        qtyIssued = qty,
                        unit = unit.trim().ifEmpty { "kg" },
                        remarks = remarks.trim().ifEmpty { null }
                    )

                    val result = MaterialsApi.submitIssue(req, token)
                    result.onSuccess { resp ->
                        isSubmitting = false
                        onSubmitSuccess(resp)
                    }.onFailure { err ->
                        isSubmitting = false
                        errorMessage = err.message ?: "Failed to issue material"
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
                    text = "Issue Material",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold
                )
            }
        }

        Spacer(modifier = Modifier.height(24.dp))
    }
}

@Composable
fun MaterialsResultScreen(
    result: MaterialIssueResponse,
    onNewIssue: () -> Unit,
    onDone: () -> Unit,
    onViewHistory: (() -> Unit)? = null
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
                    text = "MATERIAL ISSUED",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    color = Color.Gray,
                    letterSpacing = 1.sp
                )

                Spacer(modifier = Modifier.height(12.dp))

                Text(
                    text = result.issueNo,
                    fontSize = 32.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = SnmDark
                )

                Spacer(modifier = Modifier.height(16.dp))

                Box(
                    modifier = Modifier
                        .background(Color(0xFFE8F5E9), RoundedCornerShape(8.dp))
                        .border(2.dp, SnmPass, RoundedCornerShape(8.dp))
                        .padding(horizontal = 20.dp, vertical = 8.dp)
                ) {
                    Text(
                        text = "${result.remainingLotQty} ${result.unit} REMAINING",
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        fontFamily = FontFamily.Monospace,
                        color = SnmPass,
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
                        Text("Quantity Issued:", fontSize = 13.sp, color = Color.Gray)
                        Text(
                            "${result.qtyIssued} ${result.unit}",
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
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
            onClick = onNewIssue,
            modifier = Modifier
                .fillMaxWidth()
                .height(48.dp),
            shape = RoundedCornerShape(8.dp),
            colors = ButtonDefaults.buttonColors(containerColor = SnmOlive)
        ) {
            Text("Issue More Material", fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
        }

        if (onViewHistory != null) {
            Spacer(modifier = Modifier.height(10.dp))
            Button(
                onClick = onViewHistory,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp),
                shape = RoundedCornerShape(8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = SnmDark)
            ) {
                Text("View Material Issues History", fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
            }
        }

        Spacer(modifier = Modifier.height(10.dp))

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
