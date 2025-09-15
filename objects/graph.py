# Python program to print all paths from a source to destination.
from collections import defaultdict

# This class represents a directed graph
# using adjacency list representation
class Graph:

    def __init__(self, vertices, vertices_names):
        # No. of vertices
        self.V = vertices

        self.vertices_names = vertices_names

        # default dictionary to store graph
        self.graph = defaultdict(list)

        self.path = None
        self.visited = None

    # function to add an edge to graph
    def addEdge(self, u, v):
        self.graph[u].append(v)

    def printGraph(self):
        print("self.v:", self.V)
        print("self.vertices_names:", self.vertices_names)
        print("self.graph:", self.graph)

    def getgraph(self, u):
        return self.graph[u]

    '''A recursive function to print all paths from 'u' to 'd'.
    visited[] keeps track of vertices in current path.
    path[] stores actual vertices and path_index is current
    index in path[]'''

    def printAllPathsUtil(self, u, d, all_paths: list):

        # Mark the current node as visited and store in path
        self.visited[u] = True
        self.path.append(u)

        # If current vertex is same as destination, then print
        # current path[]
        if u == d:
            print(self.path)
            if all_paths != None:
                all_paths.append(self.path.copy())

        else:
            # If current vertex is not destination
            # Recur for all the vertices adjacent to this vertex
            # self.printGraph()
            for i in self.graph[u]:
                if self.visited[i] == False:
                    self.printAllPathsUtil(i, d, all_paths)

        # Remove current vertex from path[] and mark it as unvisited
        self.path.pop()
        self.visited[u] = False

    # Prints all paths from 's' to 'd'
    def printAllPaths(self, s, d, all_paths: list):
        # Mark all the vertices as not visited
        self.visited = {}
        for v in self.vertices_names:
            self.visited[v] = False

        # Create an array to store paths
        self.path = []

        # Call the recursive helper function to print all paths
        self.printAllPathsUtil(s, d, all_paths)

        self.path = None
        self.visited = None