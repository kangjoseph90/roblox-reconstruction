/* Read-only acquisition prerequisite test. No injection, task suspension or scans.
 * --pid reads only 16 bytes of dyld loader metadata if a task port is granted.
 * --self-test/--child-control read a known marker in this owned test program.
 */
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/task_info.h>
#include <libproc.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

/* ABI also used by Apple's adv_cmds/ps/tasks.c; not declared in public SDK headers. */
extern kern_return_t task_read_for_pid(task_port_t, pid_t, task_port_t *);

static const unsigned char marker[16] = "RCR_OWNED_PROBE!";

static void json_string(const char *s) {
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
        if (*p == '"' || *p == '\\') printf("\\%c", *p);
        else if (*p < 32) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

static int probe(pid_t pid, int self, int known_marker, int read_port) {
    char path[PROC_PIDPATHINFO_MAXSIZE] = {0};
    proc_pidpath(pid, path, sizeof(path));
    printf("{\"schema\":\"rcr-macos-access/v1\",\"pid\":%d,\"uid\":%d,\"target_path\":", pid, getuid());
    json_string(path);
    printf(",\"mode\":\"%s\",\"writes_to_target\":false,\"task_suspended\":false", self ? "self-test" : known_marker ? "owned-child-control" : "external-dyld-metadata");
    mach_port_t task = MACH_PORT_NULL;
    kern_return_t kr;
    if (self) { task = mach_task_self(); kr = KERN_SUCCESS; }
    else { errno = 0; kr = read_port ? task_read_for_pid(mach_task_self(), pid, &task) : task_for_pid(mach_task_self(), pid, &task); }
    int saved_errno = errno;
    printf(",\"%s\":{\"attempted\":%s,\"return_code\":%d,\"errno\":%d,\"message\":", read_port ? "task_read_for_pid" : "task_for_pid", self ? "false" : "true", kr, read_port ? saved_errno : 0);
    json_string(read_port && saved_errno ? strerror(saved_errno) : mach_error_string(kr));
    printf("}");
    if (kr != KERN_SUCCESS || task == MACH_PORT_NULL) {
        printf(",\"memory_read\":{\"attempted\":false,\"reason\":\"no_task_port\"},\"status\":\"task_port_denied\"}\n");
        return 2;
    }
    mach_vm_address_t address = (mach_vm_address_t)marker;
    if (!known_marker && !self) {
        task_dyld_info_data_t dyld;
        mach_msg_type_number_t count = TASK_DYLD_INFO_COUNT;
        kr = task_info(task, TASK_DYLD_INFO, (task_info_t)&dyld, &count);
        printf(",\"task_dyld_info\":{\"return_code\":%d,\"message\":", kr);
        json_string(mach_error_string(kr)); printf("}");
        if (kr != KERN_SUCCESS || dyld.all_image_info_addr == 0) {
            printf(",\"memory_read\":{\"attempted\":false,\"reason\":\"loader_address_unavailable\"},\"status\":\"address_unavailable\"}\n");
            mach_port_deallocate(mach_task_self(), task); return 2;
        }
        address = dyld.all_image_info_addr;
    }
    unsigned char bytes[16] = {0};
    mach_vm_size_t read_size = 0;
    kr = mach_vm_read_overwrite(task, address, sizeof(bytes), (mach_vm_address_t)bytes, &read_size);
    int match = (self || known_marker) && kr == KERN_SUCCESS && read_size == sizeof(marker) && memcmp(bytes, marker, sizeof(marker)) == 0;
    printf(",\"memory_read\":{\"attempted\":true,\"api\":\"mach_vm_read_overwrite\",\"requested_bytes\":16,\"bytes_read\":%llu,\"return_code\":%d,\"message\":", (unsigned long long)read_size, kr);
    json_string(mach_error_string(kr));
    printf(",\"content\":\"%s\",\"known_marker_match\":%s},\"status\":\"%s\"}\n", (self || known_marker) ? "owned-test-marker" : "dyld-metadata-not-saved", (self || known_marker) ? (match ? "true" : "false") : "null", kr == KERN_SUCCESS && read_size == 16 && (!(self || known_marker) || match) ? "read_succeeded" : "read_failed");
    if (!self) mach_port_deallocate(mach_task_self(), task);
    return kr == KERN_SUCCESS && read_size == 16 && (!(self || known_marker) || match) ? 0 : 2;
}

int main(int argc, char **argv) {
    setbuf(stdout, NULL);
    if (argc == 2 && strcmp(argv[1], "--self-test") == 0) return probe(getpid(), 1, 1, 0);
    if (argc == 2 && strcmp(argv[1], "--fixture") == 0) {
        printf("{\"pid\":%d,\"marker_address\":\"0x%llx\",\"marker_bytes\":16}\n", getpid(), (unsigned long long)(mach_vm_address_t)marker);
        getchar(); return 0;
    }
    if (argc == 2 && (strcmp(argv[1], "--child-control") == 0 || strcmp(argv[1], "--read-child-control") == 0)) {
        int ready[2], finish[2];
        if (pipe(ready) || pipe(finish)) { perror("pipe"); return 1; }
        pid_t child = fork();
        if (child < 0) { perror("fork"); return 1; }
        if (child == 0) {
            close(ready[0]); close(finish[1]);
            char b = 'R'; write(ready[1], &b, 1); read(finish[0], &b, 1); _exit(0);
        }
        close(ready[1]); close(finish[0]);
        char b; read(ready[0], &b, 1);
        int result = probe(child, 0, 1, strcmp(argv[1], "--read-child-control") == 0);
        write(finish[1], "X", 1); close(finish[1]); close(ready[0]); waitpid(child, NULL, 0);
        return result;
    }
    if (argc == 3 && (strcmp(argv[1], "--pid") == 0 || strcmp(argv[1], "--read-pid") == 0)) {
        char *end = NULL; errno = 0; long pid = strtol(argv[2], &end, 10);
        if (!errno && end != argv[2] && *end == 0 && pid > 0 && pid <= INT_MAX) return probe((pid_t)pid, 0, 0, strcmp(argv[1], "--read-pid") == 0);
    }
    fprintf(stderr, "usage: macos-access-probe --self-test | --child-control | --read-child-control | --pid PID | --read-pid PID | --fixture\n");
    return 1;
}
