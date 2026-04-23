# How is a Pod created when we run `kubectl`?

## 🧠 Overview

This document explains the full lifecycle of a Pod creation request in Kubernetes, from the moment `kubectl` is executed to the Pod running on a node.

---

## 🎯 30-second explanation:

When we run kubectl, it sends a REST request to the kube-apiserver. The API server authenticates and validates the request, then stores the desired Pod state in etcd.

The kube-scheduler watches for unscheduled Pods and assigns one to a suitable node based on constraints like resources, affinity, and taints.

The kubelet on that node detects the assigned Pod via the API server and uses the container runtime to create and start it.

Meanwhile, controllers in the control plane continuously ensure the actual state matches the desired state.

---

## 🚀 Step-by-Step Flow

1. **kubectl sends request**

   * `kubectl` sends a REST API request to the Kubernetes API Server.

2. **API Server processes request**

   * Authenticates and authorizes the request
   * Validates the Pod specification
   * Persists the desired state in etcd

3. **Pod is now in "Pending" state**

   * No node assigned yet

4. **Scheduler assigns a node**

   * Watches for unscheduled Pods
   * Selects a node based on:

     * Resource availability (CPU, memory)
     * Node affinity / anti-affinity
     * Taints and tolerations
   * Updates the Pod spec with `nodeName`

5. **Kubelet creates the Pod**

   * Kubelet on the selected node watches the API server
   * Detects a Pod assigned to it
   * Interacts with container runtime (e.g., containerd)
   * Pulls image and starts containers

6. **Networking is configured**

   * Pod gets an IP address
   * kube-proxy updates iptables/IPVS for Services (if applicable)

7. **Controllers ensure desired state**

   * Controller Manager monitors cluster state
   * If Pod dies, it is recreated (via ReplicaSet, Deployment, etc.)

---

## 🔄 Key Concepts

### Desired vs Actual State

* Desired state: defined by the user (e.g., 3 replicas)
* Actual state: what is currently running
* Controllers continuously reconcile the two

### Watch Mechanism

* Components like scheduler and kubelet do NOT poll
* They **watch the API server** for changes (`event-driven architecture`)

---

## 📦 What is stored in etcd?

* Pod specifications
* Cluster configuration
* Secrets and ConfigMaps
* Node information
* All cluster state (source of truth)

---

## ⚙️ Failure Scenarios

### If Scheduler is down

* Pods remain in "Pending"
* Running Pods are unaffected

### If Kubelet is down

* Node becomes NotReady
* Pods may be rescheduled elsewhere (depending on controller)

---

## 🧩 ASCII Diagram (Pod Creation Flow)

---

## ✅ TL;DR

`kubectl → API Server → etcd → Scheduler → API Server → Kubelet → Container Runtime → Pod running`

```
                         (watch)
                   +------------------+
                   |   Scheduler      |
                   +------------------+
                           |
                           | (bind Pod -> nodeName)
                           v

+-----------+      +------------------+      +--------+
|  kubectl  | ---> |   API Server     | ---> |  etcd  |
+-----------+      +------------------+      +--------+
                         ^   ^   ^
                         |   |   |
          (watch)        |   |   |        (watch)
                   +-----+   |   +-------------------+
                   |         |                       |
           +--------------+  |               +--------------+
           |  Controllers |--+               |   Kubelet   |
           +--------------+                  +--------------+
                   |                               |
                   | (reconcile)                   | (create Pod)
                   v                               v
            ensures desired state         +----------------------+
                                          | Container Runtime   |
                                          +----------------------+
                                                     |
                                                     v
                                                   [Pod]
```

NOTE:
- Kubernetes is a distributed system where components don’t call each other — they observe state changes and react.   

- Nothing talks directly to each other. Everything goes through the kube-apiserver   

- etcd is the source of truth. API server = the only gateway to it   
